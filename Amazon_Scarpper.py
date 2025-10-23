# amazon_dealhunter.py
import streamlit as st
import pandas as pd
import time
import random
import re
import os
from pathlib import Path
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

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
SCROLL_STEPS = 6
HEADLESS = True

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
st.caption("Selenium + BeautifulSoup powered scraper — safer delays, better selectors, CSV caching, email alerts")

# ----------------------------
# UTIL: driver init
# ----------------------------
def init_driver(headless=True):
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    # make it a bit more stealthy
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-infobars")
    options.add_argument("--log-level=3")
    # random user agent helps a little
    ua_list = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15"
    ]
    options.add_argument(f"--user-agent={random.choice(ua_list)}")

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    return driver

# ----------------------------
# UTIL: parse price text to float (INR)
# ----------------------------
def parse_price_text(price_text):
    """
    Extract numeric price value from amazon price snippet.
    Returns float or None.
    """
    if not price_text or not isinstance(price_text, str):
        return None
    # remove non-numeric except dots and commas
    # unified approach: extract all digits, commas, dots then normalize
    # typical price text: "₹499", "₹ 1,199", "1,299.00"
    # find chunk with digits/.,,
    m = re.search(r"[\d\.,]+", price_text.replace("\u20B9", ""))
    if not m:
        return None
    raw = m.group(0)
    raw = raw.replace(",", "")
    try:
        return float(raw)
    except:
        return None

# ----------------------------
# SCRAPER: get_best_sellers for a single category url
# ----------------------------
def get_best_sellers(url, max_price=MAX_PRICE, scroll_steps=SCROLL_STEPS, headless=HEADLESS):
    """
    Returns list of dicts: {Product Name, Price (₹), Rating, URL}
    """
    driver = None
    try:
        driver = init_driver(headless=headless)
        driver.set_page_load_timeout(30)
        driver.get(url)
        # wait for something sensible to appear: the bestseller container
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.zg-grid-general-faceout, div.p13n-sc-uncoverable-faceout, ol#zg-ordered-list"))
            )
        except Exception:
            # keep going; some pages still show content with JS slower
            pass

        # Slowly scroll to bottom to force lazy loading
        last_height = driver.execute_script("return document.body.scrollHeight")
        for i in range(scroll_steps):
            # scroll by fraction
            driver.execute_script(f"window.scrollTo(0, document.body.scrollHeight * {(i+1)/scroll_steps});")
            time.sleep(random.uniform(1.2, 2.4))

        # final small scrolls to trigger dynamic load
        for _ in range(2):
            driver.execute_script("window.scrollBy(0, 400);")
            time.sleep(1.0)

        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")

        # multiple fallback containers
        product_nodes = []
        product_nodes += soup.select(".p13n-sc-uncoverable-faceout")
        product_nodes += soup.select(".zg-grid-general-faceout")
        product_nodes += soup.select("ol#zg-ordered-list li")  # older layout
        product_nodes += soup.select(".a-section.a-spacing-none.aok-relative")  # some layouts

        # dedupe nodes (by href or text)
        seen = set()
        products = []
        for node in product_nodes:
            # attempt to find name
            name_tag = node.select_one("._cDEzb_p13n-sc-css-line-clamp-3_g3dy1, .p13n-sc-truncate, .a-link-normal.a-text-normal, .zg-item a.a-link-normal")
            # price fallbacks
            price_tag = node.select_one(".p13n-sc-price, .a-price-whole, .a-color-price")
            # rating fallbacks
            rating_tag = node.select_one(".a-icon-alt, .a-link-normal .a-icon-alt, .zg-badge-text")
            link_tag = node.select_one("a.a-link-normal, a.a-link-normal.a-text-normal")

            # fallback: try to locate a link containing '/dp/' (ASIN)
            if not link_tag:
                link_tag = node.find("a", href=re.compile(r"/dp/"))

            # get url
            url_val = None
            if link_tag and link_tag.has_attr("href"):
                href = link_tag["href"]
                if href.startswith("http"):
                    url_val = href
                else:
                    url_val = "https://www.amazon.in" + href.split("?")[0]

            # define name
            name = None
            if name_tag:
                name = name_tag.get_text(strip=True)
            else:
                # try text inside link
                if link_tag:
                    name = link_tag.get_text(strip=True)
            if not name:
                continue

            # protect duplicates
            uniq = (name[:80], url_val)
            if uniq in seen:
                continue
            seen.add(uniq)

            # price parsing: sometimes price in sibling nodes
            price = parse_price_text(price_tag.get_text(strip=True)) if price_tag else None
            if price is None:
                # search nearby spans that look like price
                sibling_price = node.find(text=re.compile(r"₹\s*\d"))
                if sibling_price:
                    price = parse_price_text(sibling_price)

            # skip if price missing or above threshold
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
        # log to streamlit console area if available
        st.write(f"Scrape error for {url}: {e}")
        return []
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

# ----------------------------
# COMBINE ALL CATEGORIES
# ----------------------------
def scrape_all_categories(categories=CATEGORIES, max_price=MAX_PRICE, headless=HEADLESS):
    all_items = []
    total = len(categories)
    for i, (cat_name, cat_url) in enumerate(categories.items(), start=1):
        st.info(f"📦 Scraping {cat_name} ({i}/{total}) ...")
        items = get_best_sellers(cat_url, max_price=max_price, headless=headless)
        for it in items:
            it["Category"] = cat_name
        all_items.extend(items)
        # small random sleep between categories so Amazon doesn't detect pattern
        time.sleep(random.uniform(1.5, 3.0))
    return pd.DataFrame(all_items)

# ----------------------------
# EMAIL SENDER (simple HTML)
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
# STREAMLIT UI
# ----------------------------
st.sidebar.header("Settings")
headless_toggle = st.sidebar.checkbox("Run headless (faster)", value=True)
price_limit = st.sidebar.number_input("Max price (₹)", min_value=1, max_value=10000, value=MAX_PRICE, step=50)
use_cache = st.sidebar.checkbox("Load cached CSV if available (faster)", value=True)
auto_email = st.sidebar.checkbox("Auto-send email after scrape (top 10 deals)", value=False)

st.sidebar.markdown("---")
st.sidebar.markdown("⚠️ Tip: Scraping frequently may trigger Amazon blocks. Use daily scheduling or caching.")

col1, col2 = st.columns([2, 1])
with col1:
    st.subheader("🔥 Run a live scrape")
    if st.button("🔁 Refresh Best Sellers (live scrape)"):
        with st.spinner("Scraping categories — this may take 30-90s depending on connection..."):
            df = scrape_all_categories(max_price=price_limit, headless=headless_toggle)
            if df.empty:
                st.warning("No items found under the price threshold. Try increasing the price or lowering headless=True.")
            else:
                df = df.sort_values(by="Price (₹)").reset_index(drop=True)
                st.success(f"Found {len(df)} items under ₹{price_limit} across categories")
                st.dataframe(df, width=1000)

                # save CSV cache
                try:
                    df.to_csv(CSV_PATH, index=False)
                    st.info(f"Saved latest results to {CSV_PATH}")
                except Exception as e:
                    st.warning(f"Could not save CSV: {e}")

                st.session_state["latest_df"] = df

                # auto-email
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
                        st.info("Provide sender/receiver credentials in the fields to auto-send email.")

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
    # make links clickable
    df_show["Product"] = df_show.apply(lambda r: f"[{r['Product Name']}]({r['URL']})", axis=1)
    display_df = df_show[["Product", "Price (₹)", "Rating", "Category"]]
    st.write("Top 12 cheapest items across scraped categories:")
    st.table(display_df.to_dict(orient="records"))
    # visual small cards
    st.markdown("#### Quick view")
    for _, row in df_show.iterrows():
        st.markdown(f"**[{row['Product Name']}]({row['URL']})** — ₹{row['Price (₹)']} • {row['Rating']} • `{row['Category']}`")
else:
    st.info("No results yet. Click *Refresh Best Sellers* to scrape live.")

st.markdown("---")
st.caption("Built with ❤️ by Ommi + Buddy (GPT). Keep scraping ethically and don't overload Amazon with requests.")
