import json
import time
import requests
import pandas as pd
from bs4 import BeautifulSoup

# Base API Endpoint
BASE_URL = "https://eastoceansg.com/collections/tropical-freshwater-fish/products.json"

# Expected target columns from the product characteristics table
TARGET_COLUMNS = [
    "Common Name",
    "Scientific Name",
    "Care Level",
    "Maximum Size",
    "pH",
    "Temperature",
    "Life Span",
    "Behaviour",
    "Tank Region",
    "Gender",
]

def parse_characteristics_table(html_content):
    """
    Parses the <table> inside body_html and returns a dictionary 
    mapped to the target characteristic headers.
    """
    characteristics = {col: "N/A" for col in TARGET_COLUMNS}
    
    if not html_content:
        return characteristics

    soup = BeautifulSoup(html_content, "html.parser")
    table = soup.find("table")
    
    if not table:
        return characteristics

    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) >= 2:
            key = cells[0].get_text(strip=True)
            value = cells[1].get_text(strip=True)
            
            # Map scraped keys to expected column names
            for col in TARGET_COLUMNS:
                if key.lower() == col.lower():
                    characteristics[col] = value
                    break

    return characteristics


def scrape_all_pages(start_page=1, end_page=7):
    all_fishes = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    for page in range(start_page, end_page + 1):
        print(f"Fetching Page {page} of {end_page}...")
        url = f"{BASE_URL}?page={page}"
        
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code != 200:
                print(f"⚠️ Page {page} returned status code {response.status_code}. Skipping.")
                continue

            data = response.json()
            products = data.get("products", [])

            if not products:
                print(f"No products found on page {page}. Stopping.")
                break

            for product in products:
                title = product.get("title", "")
                handle = product.get("handle", "")
                product_url = f"https://eastoceansg.com/products/{handle}"
                
                # Extract primary image URL
                images = product.get("images", [])
                img_url = images[0].get("src") if images else "N/A"

                # Extract price from first variant
                variants = product.get("variants", [])
                price = variants[0].get("price") if variants else "N/A"

                # Parse table inside body_html
                body_html = product.get("body_html", "")
                table_data = parse_characteristics_table(body_html)

                # Combine base shopify data + table characteristics
                fish_entry = {
                    "Title": title,
                    "Price (SGD)": price,
                    "Product URL": product_url,
                    "Image URL": img_url,
                    **table_data  # Unpacks all parsed table columns
                }

                all_fishes.append(fish_entry)

            # Be polite to the server
            time.sleep(1)

        except Exception as e:
            print(f"❌ Error fetching page {page}: {e}")

    return all_fishes


if __name__ == "__main__":
    # Prerequisites verification
    # pip install requests bs4 pandas
    
    print("🚀 Starting fish catalog scraping...")
    dataset = scrape_all_pages(start_page=1, end_page=7)
    
    print(f"\n✅ Total records extracted: {len(dataset)}")

    # 1. Export to JSON
    json_filename = "fish_characteristics.json"
    with open(json_filename, "w", encoding="utf-8") as f:
        json.dump(dataset, f, indent=2, ensure_ascii=False)
    print(f"💾 Saved JSON to {json_filename}")

    # 2. Export to CSV
    csv_filename = "fish_characteristics.csv"
    df = pd.DataFrame(dataset)
    df.to_csv(csv_filename, index=False, encoding="utf-8-sig")
    print(f"💾 Saved CSV to {csv_filename}")