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
        
        page_text = soup.get_text(" ", strip=True)
        normalized_text = normalize_text(page_text)
        emails = extract_emails(normalized_text)
        
        found_data = []
        for email in emails:
            found_data.append({
                "Email": email,
                "Page URL": final_url,
                "Page Title": title
            })
            
        links = set()
        for tag in soup.find_all("a", href=True):
            link = urljoin(final_url, tag["href"])
            parsed = urlparse(link)
            clean_link = parsed.scheme + "://" + parsed.netloc + parsed.path
            links.add(clean_link)
            
        return found_data, links

    except Exception:
        return [], []

# ---------------- STREAMLIT UI ---------------- #

st.set_page_config(page_title="Fast Email Crawler", page_icon="⚡", layout="wide")

st.title("⚡ High-Speed Email Extractor")
st.markdown("Multi-threaded crawler with **Real-time Filtering**.")

with st.sidebar:
    st.header("⚙️ Settings")
    start_url = st.text_input("Start URL", "https://codewila.com/")
    max_pages = st.slider("Max Pages", 10, 500, 100)
    workers = st.slider("Speed (Threads)", 5, 50, 20)
    timeout = st.number_input("Timeout (s)", value=5)
    
    st.write("---")
    st.header("🔍 Filters")
    # Yahan filter select karein
    remove_duplicates = st.checkbox("Remove Duplicate Emails", value=True)

if st.button("🚀 Start Fast Crawl", type="primary"):
    
    if not start_url:
        st.error("URL daalo bhai!")
        st.stop()

    # Setup
    base_domain = urlparse(start_url).netloc
    visited_urls = set([start_url])
    
    # Ye hai main data store
    all_emails = []       # Final list
    seen_emails = set()   # Sirf tracking ke liye (agar duplicate remove karna ho)
    
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
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    pages_scanned = 0
    start_time = time.time()
    
    # ---------------- THREAD POOL LOOP ---------------- #
    
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
                    
                    # --- LOGIC CHANGE HERE (Filter before adding) ---
                    if data:
                        if remove_duplicates:
                            for item in data:
                                email = item['Email']
                                # Agar ye email pehle nahi mila, tabhi add karo
                                if email not in seen_emails:
                                    seen_emails.add(email)
                                    all_emails.append(item)
                        else:
                            # Agar filter OFF hai, to sab add karo
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

                # UI Updates
                if pages_scanned % 5 == 0 or pages_scanned == max_pages:
                    elapsed = time.time() - start_time
                    progress = min(pages_scanned / max_pages, 1.0)
                    
                    bar.progress(progress)
                    status_text.text(f"Scanning: {url}")
                    
                    metric_pages.metric("Pages Scanned", pages_scanned)
                    metric_time.metric("Time Taken", f"{elapsed:.1f}s")
                    
                    # Ab yahan direct len(all_emails) use kar sakte hain
                    # Kyunki all_emails mein ab duplicate hai hi nahi
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