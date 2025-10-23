# amazon_dealhunter.py
import streamlit as st
import pandas as pd
import time
import random
import re
import os
from pathlib import Path
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ----------------------------
# CONFIG / CONSTANTS
# ----------------------------
APP_DIR = Path(__file__).parent
CSV_PATH = APP_DIR / "bestsellers_latest.csv"
MAX_PRICE = 500  # change to 1000 if you want; spec asked for under ₹500

CATEGORIES = {
    "Books": "https://www.amazon.in/gp/bestsellers/books/",
    "Toys & Games": "https://www.amazon.in/gp/bestsellers/toys/",
    "Home & Kitchen": "https://www.amazon.in/gp/bestsellers/kitchen/",
    "Beauty": "https://www.amazon.in/gp/bestsellers/beauty/",
    "Electronics": "https://www.amazon.in/gp/bestsellers/electronics/",
    "Grocery": "https://www.amazon.in/gp/bestsellers/grocery/",
    "Stationery": "https://www.amazon.in/gp/bestsellers/office-products/",
    "Fashion": "https://www.amazon.in/gp/bestsellers/fashion/",
    "Sports": "https://www.amazon.in/gp/bestsellers/sports/"
}

st.set_page_config(page_title="Amazon DealHunter — Best Sellers < ₹500", layout="wide")
st.title("🛍️ Amazon DealHunter — Best Sellers under ₹500")
st.caption("Requests + BeautifulSoup powered scraper — faster, Streamlit Cloud friendly, CSV caching, email alerts")

# ----------------------------
# UTIL: parse price text to float (INR)
# ----------------------------
def parse_price_text(price_text):
    if not price_text or not isinstance(price_text, str):
        return None
    m = re.search(r"[\d\.,]+", price_text.replace("\u20B9", ""))
    if not m:
        return None
    raw = m.group(0).replace(",", "")
    try:
        return float(raw)
    except:
        return None

# ----------------------------
# SCRAPER: get_best_sellers for a single category url (requests version)
# ----------------------------
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/115.0 Safari/537.36"
}

def get_best_sellers(url, max_price=MAX_PRICE):
    products = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # multiple fallback containers
        product_nodes = []
        product_nodes += soup.select(".p13n-sc-uncoverable-faceout")
        product_nodes += soup.select(".zg-grid-general-faceout")
        product_nodes += soup.select("ol#zg-ordered-list li")
        product_nodes += soup.select(".a-section.a-spacing-none.aok-relative")

        seen = set()
        for node in product_nodes:
            name_tag = node.select_one("._cDEzb_p13n-sc-css-line-clamp-3_g3dy1, .p13n-sc-truncate, .a-link-normal.a-text-normal, .zg-item a.a-link-normal")
            price_tag = node.select_one(".p13n-sc-price, .a-price-whole, .a-color-price")
            rating_tag = node.select_one(".a-icon-alt, .a-link-normal .a-icon-alt, .zg-badge-text")
            link_tag = node.select_one("a.a-link-normal, a.a-link-normal.a-text-normal")

            if not link_tag:
                link_tag = node.find("a", href=re.compile(r"/dp/"))

            url_val = None
            if link_tag and link_tag.has_attr("href"):
                href = link_tag["href"]
                url_val = href if href.startswith("http") else "https://www.amazon.in" + href.split("?")[0]

            name = name_tag.get_text(strip=True) if name_tag else (link_tag.get_text(strip=True) if link_tag else None)
            if not name:
                continue

            uniq = (name[:80], url_val)
            if uniq in seen:
                continue
            seen.add(uniq)

            price = parse_price_text(price_tag.get_text(strip=True)) if price_tag else None
            if price is None:
                sibling_price = node.find(text=re.compile(r"₹\s*\d"))
                if sibling_price:
                    price = parse_price_text(sibling_price)

            if price is None or price > max_price:
                continue

            rating = rating_tag.get_text(strip=True) if rating_tag else "N/A"
            products.append({
                "Product Name": name,
                "Price (₹)": price,
                "Rating": rating,
                "URL": url_val or "N/A"
            })
        return products
    except Exception as e:
        st.write(f"Scrape error for {url}: {e}")
        return []

# ----------------------------
# COMBINE ALL CATEGORIES
# ----------------------------
def scrape_all_categories(categories=CATEGORIES, max_price=MAX_PRICE):
    all_items = []
    total = len(categories)
    for i, (cat_name, cat_url) in enumerate(categories.items(), start=1):
        st.info(f"📦 Scraping {cat_name} ({i}/{total}) ...")
        items = get_best_sellers(cat_url, max_price=max_price)
        for it in items:
            it["Category"] = cat_name
        all_items.extend(items)
        time.sleep(random.uniform(1.0, 2.0))  # delay to mimic human
    return pd.DataFrame(all_items)

# ----------------------------
# EMAIL SENDER (same)
# ----------------------------
def send_email_html(df: pd.DataFrame, sender_email: str, sender_pass: str, receiver_email: str, subject=None):
    subject = subject or f"Amazon DealHunter — Top {len(df)} deals under ₹{MAX_PRICE} ({datetime.now().date()})"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender_email
    msg["To"] = receiver_email

    html_table = df.to_html(index=False, escape=False)
    html = f"""
    <html>
      <body>
        <h3>{subject}</h3>
        {html_table}
        <p>— Generated by Amazon DealHunter</p>
      </body>
    </html>
    """
    msg.attach(MIMEText(html, "html"))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(sender_email, sender_pass)
        server.sendmail(sender_email, receiver_email, msg.as_string())
        server.quit()
        return True, "Email sent"
    except Exception as e:
        return False, str(e)

# ----------------------------
# STREAMLIT UI (unchanged)
# ----------------------------
st.sidebar.header("Settings")
price_limit = st.sidebar.number_input("Max price (₹)", min_value=1, max_value=10000, value=MAX_PRICE, step=50)
use_cache = st.sidebar.checkbox("Load cached CSV if available (faster)", value=True)
auto_email = st.sidebar.checkbox("Auto-send email after scrape (top 10 deals)", value=False)

st.sidebar.markdown("---")
st.sidebar.markdown("⚠️ Tip: Scraping frequently may trigger Amazon blocks. Use daily scheduling or caching.")

col1, col2 = st.columns([2, 1])
with col1:
    st.subheader("🔥 Run a live scrape")
    if st.button("🔁 Refresh Best Sellers (live scrape)"):
        with st.spinner("Scraping categories — this may take 10-30s depending on connection..."):
            df = scrape_all_categories(max_price=price_limit)
            if df.empty:
                st.warning("No items found under the price threshold. Try increasing the price.")
            else:
                df = df.sort_values(by="Price (₹)").reset_index(drop=True)
                st.success(f"Found {len(df)} items under ₹{price_limit} across categories")
                st.dataframe(df, width=1000)

                try:
                    df.to_csv(CSV_PATH, index=False)
                    st.info(f"Saved latest results to {CSV_PATH}")
                except Exception as e:
                    st.warning(f"Could not save CSV: {e}")

                st.session_state["latest_df"] = df

                if auto_email:
                    top_email_df = df.nsmallest(10, "Price (₹)")
                    sender = st.text_input("Sender Email (Gmail) for sending", key="auto_sender")
                    sender_pass = st.text_input("Sender App Password", type="password", key="auto_pass")
                    receiver = st.text_input("Receiver Email", key="auto_receiver")
                    if sender and sender_pass and receiver:
                        ok, msg = send_email_html(top_email_df, sender, sender_pass, receiver)
                        if ok:
                            st.success("Auto-email sent (top 10 deals).")
                        else:
                            st.error(f"Auto-email failed: {msg}")
                    else:
                        st.info("Provide sender/receiver credentials to auto-send email.")

with col2:
    st.subheader("Quick load / Email")
    if use_cache and CSV_PATH.exists():
        cached_df = pd.read_csv(CSV_PATH)
        st.write(f"📥 Cached results loaded ({len(cached_df)} rows) — last saved: {datetime.fromtimestamp(CSV_PATH.stat().st_mtime)}")
        st.dataframe(cached_df)
        st.session_state["latest_df"] = cached_df

    st.markdown("---")
    st.write("📤 Email top deals manually")
    if "latest_df" in st.session_state and not st.session_state["latest_df"].empty:
        st.write(f"Latest scraped items: {len(st.session_state['latest_df'])}")
        send_top_n = st.selectbox("How many top deals to email?", options=[5, 10, 20, 50, 100, 200], index=1)
        sender_email = st.text_input("Sender Gmail (for SMTP)", key="sender_email")
        sender_pass = st.text_input("Sender App Password (Gmail)", type="password", key="sender_pass")
        recv_email = st.text_input("Receiver Email", key="recv_email")
        if st.button("📧 Send Top Deals Email"):
            if not (sender_email and sender_pass and recv_email):
                st.error("Please fill sender, app password, and receiver fields.")
            else:
                send_df = st.session_state["latest_df"].nsmallest(send_top_n, "Price (₹)")
                ok, msg = send_email_html(send_df, sender_email, sender_pass, recv_email)
                if ok:
                    st.success("Email sent successfully!")
                else:
                    st.error(f"Failed to send email: {msg}")
    else:
        st.info("No cached or scraped data available yet. Run a live scrape or load cache.")

st.markdown("---")
st.subheader("Top cheap gems")
if "latest_df" in st.session_state and not st.session_state["latest_df"].empty:
    df_show = st.session_state["latest_df"].nsmallest(12, "Price (₹)").copy()
    df_show["Product"] = df_show.apply(lambda r: f"[{r['Product Name']}]({r['URL']})", axis=1)
    display_df = df_show[["Product", "Price (₹)", "Rating", "Category"]]
    st.write("Top 12 cheapest items across scraped categories:")
    st.table(display_df.to_dict(orient="records"))
    st.markdown("#### Quick view")
    for _, row in df_show.iterrows():
        st.markdown(f"**[{row['Product Name']}]({row['URL']})** — ₹{row['Price (₹)']} • {row['Rating']} • `{row['Category']}`")
else:
    st.info("No results yet. Click *Refresh Best Sellers* to scrape live.")

st.markdown("---")
st.caption("Built with ❤️ by Ommi + Buddy (GPT). Keep scraping ethically and don't overload Amazon with requests.")
