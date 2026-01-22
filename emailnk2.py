import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import re
import pandas as pd
import concurrent.futures
import time

# ---------------- CONFIG & UTILS ---------------- #

EMAIL_REGEX = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"

OBFUSCATED_PATTERNS = [
    (r"\s*\[at\]\s*", "@"),
    (r"\s*\(at\)\s*", "@"),
    (r"\s+at\s+", "@"),
    (r"\s*\[dot\]\s*", "."),
    (r"\s*\(dot\)\s*", "."),
    (r"\s+dot\s+", "."),
]

def normalize_text(text):
    text = text.lower()
    for pattern, replacement in OBFUSCATED_PATTERNS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text

# --- NEW FUNCTION: Decode Cloudflare Emails ---
def decode_cf_email(cf_email):
    email = ""
    try:
        r = int(cf_email[:2], 16)
        for i in range(2, len(cf_email), 2):
            c = int(cf_email[i:i+2], 16) ^ r
            email += chr(c)
    except:
        return None
    return email

def extract_emails(text):
    return set(re.findall(EMAIL_REGEX, text))

def is_internal_link(link, base_domain):
    parsed = urlparse(link)
    return parsed.netloc == "" or parsed.netloc == base_domain

# ---------------- CRAWLER LOGIC ---------------- #

def crawl_page(url, session, timeout):
    try:
        response = session.get(url, timeout=timeout, verify=False)
        if response.status_code != 200:
            return [], []
        
        final_url = response.url
        soup = BeautifulSoup(response.text, "lxml")
        title = soup.title.string.strip() if soup.title else "N/A"
        
        found_emails = set()

        # 1. CLOUDFLARE DECODING (New Logic)
        # Cloudflare hides emails in 'data-cfemail' attribute
        for cf in soup.find_all(attrs={"data-cfemail": True}):
            decoded = decode_cf_email(cf['data-cfemail'])
            if decoded:
                found_emails.add(decoded)

        # 2. MAILTO EXTRACTION (New Logic)
        # Check all links that start with 'mailto:'
        for a in soup.find_all('a', href=True):
            if a['href'].lower().startswith('mailto:'):
                # Clean up the mailto string (remove ?subject= etc)
                possible_email = a['href'].split(':')[1].split('?')[0]
                if re.match(EMAIL_REGEX, possible_email):
                    found_emails.add(possible_email)

        # 3. STANDARD TEXT EXTRACTION (Old Logic)
        page_text = soup.get_text(" ", strip=True)
        normalized_text = normalize_text(page_text)
        text_emails = extract_emails(normalized_text)
        found_emails.update(text_emails)
        
        # Prepare Data
        found_data = []
        for email in found_emails:
            # Basic filter to remove garbage/too long strings
            if len(email) < 50: 
                found_data.append({
                    "Email": email,
                    "Page URL": final_url,
                    "Page Title": title
                })
            
        links = set()
        for tag in soup.find_all("a", href=True):
            link = urljoin(final_url, tag["href"])
            parsed = urlparse(link)
            # Remove queries/fragments for better crawling
            clean_link = parsed.scheme + "://" + parsed.netloc + parsed.path
            links.add(clean_link)
            
        return found_data, links

    except Exception as e:
        return [], []

# ---------------- STREAMLIT UI ---------------- #

st.set_page_config(page_title="Fast Email Crawler", page_icon="⚡", layout="wide")

st.title("⚡ High-Speed Email Extractor")
st.markdown("Multi-threaded crawler with **Cloudflare Decoding** & **Real-time Filtering**.")

with st.sidebar:
    st.header("⚙️ Settings")
    start_url = st.text_input("Start URL", "https://www.ecraftsmen.com/contact-us")
    max_pages = st.slider("Max Pages", 10, 500, 100)
    workers = st.slider("Speed (Threads)", 5, 50, 20)
    timeout = st.number_input("Timeout (s)", value=5)
    
    st.write("---")
    st.header("🔍 Filters")
    remove_duplicates = st.checkbox("Remove Duplicate Emails", value=True)

if st.button("🚀 Start Fast Crawl", type="primary"):
    
    if not start_url:
        st.error("URL daalo bhai!")
        st.stop()

    # Setup
    base_domain = urlparse(start_url).netloc
    visited_urls = set([start_url])
    
    all_emails = []       
    seen_emails = set()   
    
    # UI Containers
    status_text = st.empty()
    bar = st.progress(0)
    
    col1, col2, col3 = st.columns(3)
    metric_pages = col1.empty()
    metric_emails = col2.empty()
    metric_time = col3.empty()
    
    st.write("### 👀 Live Results (Unique)")
    live_table = st.empty()
    
    requests.packages.urllib3.disable_warnings()
    session = requests.Session()
    # Headers zaruri hain taaki bot detection kam ho
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    })

    pages_scanned = 0
    start_time = time.time()
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_url = {executor.submit(crawl_page, start_url, session, timeout): start_url}
        
        while future_to_url and pages_scanned < max_pages:
            done, not_done = concurrent.futures.wait(
                future_to_url.keys(), 
                return_when=concurrent.futures.FIRST_COMPLETED
            )
            
            for future in done:
                url = future_to_url.pop(future)
                pages_scanned += 1
                
                try:
                    data, links = future.result()
                    
                    if data:
                        if remove_duplicates:
                            for item in data:
                                email = item['Email']
                                if email not in seen_emails:
                                    seen_emails.add(email)
                                    all_emails.append(item)
                        else:
                            all_emails.extend(data)
                    
                    if pages_scanned + len(future_to_url) < max_pages:
                        for link in links:
                            if is_internal_link(link, base_domain) and link not in visited_urls:
                                visited_urls.add(link)
                                new_future = executor.submit(crawl_page, link, session, timeout)
                                future_to_url[new_future] = link
                                if len(visited_urls) >= max_pages: 
                                    break
                except Exception:
                    pass

                if pages_scanned % 5 == 0 or pages_scanned == max_pages:
                    elapsed = time.time() - start_time
                    progress = min(pages_scanned / max_pages, 1.0)
                    
                    bar.progress(progress)
                    status_text.text(f"Scanning: {url}")
                    metric_pages.metric("Pages Scanned", pages_scanned)
                    metric_time.metric("Time Taken", f"{elapsed:.1f}s")
                    metric_emails.metric("Emails Found", len(all_emails))
                    
                    if all_emails:
                        live_table.dataframe(pd.DataFrame(all_emails), height=300, use_container_width=True)

            if pages_scanned >= max_pages:
                for f in future_to_url: f.cancel()
                break

    end_time = time.time()
    duration = round(end_time - start_time, 2)
    
    bar.progress(1.0)
    status_text.success(f"✅ Finished! Scanned {pages_scanned} pages in {duration} seconds.")

    if all_emails:
        df = pd.DataFrame(all_emails)
        csv = df.to_csv(index=False).encode('utf-8')
        st.download_button("⬇️ Download CSV", csv, f"emails_{base_domain}.csv", "text/csv")
    else:
        st.warning("No emails found.")

