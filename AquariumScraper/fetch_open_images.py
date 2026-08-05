import io
import time
import urllib.parse
import pandas as pd
import requests

# Set User-Agent to comply with Wikimedia/GBIF/iNaturalist API policies
HEADERS = {
    "User-Agent": "FishDictionaryApp/1.0 (educational_bot@example.com)"
}

INPUT_FILE = "fish_characteristics_enhanced.csv"
OUTPUT_FILE = "fish_characteristics_open_media.csv"


def get_wikimedia_image(query):
    """Search Wikimedia Commons for species image."""
    try:
        url = (
            "https://commons.wikimedia.org/w/api.php?"
            f"action=query&generator=search&gsrsearch={urllib.parse.quote(query)}"
            "&gsrnamespace=6&prop=imageinfo&iiprop=url&format=json&gsrlimit=1"
        )
        res = requests.get(url, headers=HEADERS, timeout=5)
        if res.status_code == 200:
            data = res.json()
            pages = data.get("query", {}).get("pages", {})
            for _, page_info in pages.items():
                image_info = page_info.get("imageinfo", [])
                if image_info and "url" in image_info[0]:
                    return image_info[0]["url"]
    except Exception as e:
        print(f"    [Wikimedia Error]: {e}")
    return None


def get_inaturalist_image(query):
    """Search iNaturalist Taxa API for crowdsourced CC-licensed photos."""
    try:
        url = f"https://api.inaturalist.org/v1/taxa?q={urllib.parse.quote(query)}&per_page=1"
        res = requests.get(url, headers=HEADERS, timeout=5)
        if res.status_code == 200:
            data = res.json()
            results = data.get("results", [])
            if results and results[0].get("default_photo"):
                photo = results[0]["default_photo"]
                photo_url = photo.get("medium_url") or photo.get("url")
                if photo_url:
                    # Upgrade image quality from square thumbnail to medium view
                    return photo_url.replace("square", "medium")
    except Exception as e:
        print(f"    [iNaturalist Error]: {e}")
    return None


def get_gbif_image(query):
    """Search GBIF (Global Biodiversity Information Facility) occurrence media."""
    try:
        # Step 1: Match species key
        match_url = f"https://api.gbif.org/v1/species/match?name={urllib.parse.quote(query)}"
        res = requests.get(match_url, headers=HEADERS, timeout=5)
        if res.status_code == 200:
            match_data = res.json()
            taxon_key = match_data.get("usageKey")

            if taxon_key:
                # Step 2: Search occurrences with StillImage media
                occ_url = f"https://api.gbif.org/v1/occurrence/search?taxonKey={taxon_key}&mediaType=StillImage&limit=1"
                occ_res = requests.get(occ_url, headers=HEADERS, timeout=5)
                if occ_res.status_code == 200:
                    occ_data = occ_res.json()
                    results = occ_data.get("results", [])
                    if results and results[0].get("media"):
                        for media in results[0]["media"]:
                            if (
                                media.get("type") == "StillImage"
                                and "identifier" in media
                            ):
                                return media["identifier"]
    except Exception as e:
        print(f"    [GBIF Error]: {e}")
    return None


def fetch_image_cascade(title, common_name):
    """
    Cascades search through Wikimedia Commons -> iNaturalist -> GBIF.
    Returns (image_url, source_provider)
    """
    search_queries = []

    # Priority 1: First name from Common Name
    if pd.notna(common_name) and str(common_name).strip():
        primary_common = str(common_name).split(",")[0].strip()
        search_queries.append(primary_common)

    # Priority 2: Full Title
    if pd.notna(title) and str(title).strip():
        clean_title = str(title).split("(")[0].strip()  # Remove parenthetical notes
        if clean_title not in search_queries:
            search_queries.append(clean_title)

    for query in search_queries:
        print(f"  🔍 Querying open APIs for: '{query}'")

        # 1. Wikimedia Commons
        img_url = get_wikimedia_image(query)
        if img_url:
            return img_url, "Wikimedia Commons"

        # 2. iNaturalist
        img_url = get_inaturalist_image(query)
        if img_url:
            return img_url, "iNaturalist"

        # 3. GBIF / FishBase
        img_url = get_gbif_image(query)
        if img_url:
            return img_url, "GBIF"

        time.sleep(0.3)  # Rate limiting courtesy pause

    return None, None


def main():
    try:
        df = pd.read_csv(INPUT_FILE)
        print(f"📖 Loaded {len(df)} records from '{INPUT_FILE}'.\n")
    except FileNotFoundError:
        print(
            f"❌ File '{INPUT_FILE}' not found. Please ensure it exists in the current directory."
        )
        return

    updated_count = 0

    for idx, row in df.iterrows():
        title = row.get("Title", "")
        common_name = row.get("Common Name", "")

        print(f"[{idx+1}/{len(df)}] Processing: {title}")

        found_url, provider = fetch_image_cascade(title, common_name)

        if found_url:
            print(f"  ✅ Match found via [{provider}]: {found_url}\n")
            df.at[idx, "Image URL"] = found_url
            df.at[idx, "Product URL"] = found_url  # Overwrite Product URL as requested
            updated_count += 1
        else:
            print(f"  ⚠️ No open media found for '{title}'. Keeping original URLs.\n")

    # Save to new CSV
    df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    print("=" * 60)
    print(
        f"🎉 Success! Updated {updated_count}/{len(df)} records with open-source media."
    )
    print(f"💾 Exported output to '{OUTPUT_FILE}'.")


if __name__ == "__main__":
    main()