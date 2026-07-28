import io
import json
import pandas as pd

# Define target columns matching the dataset schema
COLUMNS = [
    "Title",
    "Product URL",
    "Image URL",
    "Common Name",
    "Care Level",
    "Maximum Size",
    "pH",
    "Temperature",
    "Life Span",
    "Behaviour",
    "Tank Region",
    "Gender",
]

# Raw CSV data containing all 15 outdoor pond fish entries
POND_FISH_CSV = """Koi (Nishikigoi),https://eastoceansg.com/products/koi-nishikigoi,https://cdn.shopify.com/s/files/1/0430/9870/1992/products/koi.jpg,"Koi, Nishikigoi, Japanese Carp",Moderate,75 - 90 cm,7.0 - 8.5,15°C - 26°C,25 - 50+ years,"Peaceful, highly social, active swimmer",All levels,"Females are generally rounder and broader; males develop breeding tubercles on gill covers."
Comet Goldfish,https://eastoceansg.com/products/comet-goldfish,https://cdn.shopify.com/s/files/1/0430/9870/1992/products/comet.jpg,"Comet Goldfish, Single-Tail Goldfish",Easy,30 cm,6.8 - 7.8,18°C - 24°C,10 - 15 years,"Fast, hardy, active community swimmer",All levels,"Males display small white spots (tubercles) on pectoral fins and gill covers during spawning."
Shubunkin Goldfish,https://eastoceansg.com/products/shubunkin-goldfish,https://cdn.shopify.com/s/files/1/0430/9870/1992/products/shubunkin.jpg,"Shubunkin, Calico Goldfish, Speckled Goldfish",Easy,25 - 30 cm,6.8 - 7.8,18°C - 24°C,10 - 15 years,"Peaceful, fast swimmer, hardy calico pond fish",All levels,"Females appear fuller-bodied and rounded when carrying eggs."
Fantail Goldfish,https://eastoceansg.com/products/fantail-goldfish,https://cdn.shopify.com/s/files/1/0430/9870/1992/products/fantail.jpg,"Fantail, Double-tail Goldfish",Easy - Moderate,15 - 20 cm,6.5 - 7.5,18°C - 24°C,8 - 12 years,"Peaceful, slower swimmer than single-tail varieties",Middle to Bottom,"Females are rounder; males develop white breeding tubercles on gill plates."
Weather Loach / Dojo Loach,https://eastoceansg.com/products/dojo-loach,https://cdn.shopify.com/s/files/1/0430/9870/1992/products/dojo.jpg,"Dojo Loach, Weather Loach, Oriental Weatherfish",Easy,20 - 25 cm,6.5 - 7.5,10°C - 25°C,8 - 10 years,"Peaceful bottom scavenger, responds actively to barometric pressure shifts",Bottom,"Males have larger, triangular-shaped pectoral fins."
Rosy Red Minnow,https://eastoceansg.com/products/rosy-red-minnow,https://cdn.shopify.com/s/files/1/0430/9870/1992/products/rosyred.jpg,"Fathead Minnow, Rosy Red Minnow",Very Easy,5 - 8 cm,7.0 - 8.0,10°C - 26°C,2 - 4 years,"Peaceful schooling dither fish, great mosquito larva eater",Middle to Top,"Breeding males develop dark coloration and pad-like spongy structures on their heads."
Golden Orfe,https://eastoceansg.com/products/golden-orfe,https://cdn.shopify.com/s/files/1/0430/9870/1992/products/orfe.jpg,"Golden Orfe, Ide",Moderate,40 - 60 cm,6.8 - 8.0,10°C - 22°C,15 - 20 years,"Extremely fast, surface schooling fish requiring high dissolved oxygen",Surface to Middle,"Females grow larger and heavier-bodied when mature."
Golden Tench,https://eastoceansg.com/products/golden-tench,https://cdn.shopify.com/s/files/1/0430/9870/1992/products/tench.jpg,"Golden Tench, Doctor Fish",Easy,30 - 45 cm,6.5 - 8.0,10°C - 24°C,15 - 20 years,"Peaceful, shy bottom dweller, historically believed to heal other fish",Bottom,"Males have significantly larger and thicker pelvic fins."
Sailfin Molly,https://eastoceansg.com/products/sailfin-molly,https://cdn.shopify.com/s/files/1/0430/9870/1992/products/sailfin_molly.jpg,"Sailfin Molly, Giant Molly",Easy,10 - 12 cm,7.5 - 8.5,22°C - 28°C,3 - 5 years,"Active, peaceful livebearer, excellent algae and mosquito eater for patio tubs",Middle to Top,"Males possess a prominent sail-like dorsal fin and a gonopodium."
Guppy / Endler's Livebearer,https://eastoceansg.com/products/guppy,https://cdn.shopify.com/s/files/1/0430/9870/1992/products/guppy.jpg,"Guppy, Rainbowfish, Millions Fish",Very Easy,4 - 6 cm,7.0 - 8.0,22°C - 28°C,2 - 3 years,"Peaceful, rapid livebearer breeder, top-tier mosquito control for container ponds",Top,"Males are smaller with vivid coloration and a gonopodium; females are larger and duller."
Paradise Fish,https://eastoceansg.com/products/paradise-fish,https://cdn.shopify.com/s/files/1/0430/9870/1992/products/paradise_fish.jpg,"Paradise Gourami, Paradise Fish",Easy - Moderate,8 - 10 cm,6.0 - 8.0,16°C - 26°C,5 - 8 years,"Hardy air-breathing labyrinth fish, can be semi-aggressive toward tankmates",Top,"Males feature longer fin filaments and significantly brighter coloration."
Giant Gourami,https://eastoceansg.com/products/giant-gourami,https://cdn.shopify.com/s/files/1/0430/9870/1992/products/giant_gourami.jpg,"Giant Gourami, Osphronemus",Easy - Moderate,45 - 60 cm,6.5 - 7.8,20°C - 30°C,10 - 20+ years,"Large, intelligent, mostly peaceful but requires massive pond volume",All levels,"Males develop a prominent nuchal hump on their forehead and pointed dorsal/anal fins."
Tinfoil Barb,https://eastoceansg.com/products/tinfoil-barb,https://cdn.shopify.com/s/files/1/0430/9870/1992/products/tinfoil_barb.jpg,"Tinfoil Barb, Gold Tinfoil",Easy,30 - 35 cm,6.5 - 7.5,22°C - 28°C,8 - 10 years,"Fast, active schooling fish, requires large open swimming areas in outdoor ponds",Middle,"Difficult to sex visually; females are slightly rounder when egg-laden."
Common Pleco,https://eastoceansg.com/products/common-pleco,https://cdn.shopify.com/s/files/1/0430/9870/1992/products/common_pleco.jpg,"Common Plecostomus, Leopard Sailfin Pleco",Easy,30 - 50 cm,6.5 - 7.8,20°C - 28°C,10 - 15+ years,"Solitary bottom-dwelling nocturnal algae scraper and scavenger",Bottom,"Hard to sex visually; mature males may show small odontodes on pectoral rays."
Ranchu Goldfish,https://eastoceansg.com/products/ranchu-goldfish,https://cdn.shopify.com/s/files/1/0430/9870/1992/products/ranchu.jpg,"Ranchu, King of Goldfish",Moderate,12 - 18 cm,6.5 - 7.5,18°C - 24°C,6 - 10 years,"Slow swimmer without a dorsal fin, best suited for shallow garden ponds or tubs",Middle to Bottom,"Females have rounder bellies; males develop white tubercles on pectoral fins during spawning."
"""


def merge_datasets(
    input_file="fish_characteristics.csv",
    output_csv="fish_characteristics_enhanced.csv",
    output_json="fish_characteristics_enhanced.json",
):

    # 1. Read existing CSV if available
    try:
        df_existing = pd.read_csv(input_file)
        print(f"📖 Loaded {len(df_existing)} existing entries from {input_file}")
    except FileNotFoundError:
        print(
            f"⚠️ File '{input_file}' not found. Starting with a new dataset..."
        )
        df_existing = pd.DataFrame(columns=COLUMNS)

    # 2. Parse new pond fish CSV string directly via StringIO
    df_new = pd.read_csv(io.StringIO(POND_FISH_CSV.strip()), names=COLUMNS)
    print(f"➕ Added {len(df_new)} new pond fish entries")

    # 3. Concatenate and drop duplicates based on "Title"
    df_merged = pd.concat([df_existing, df_new], ignore_index=True)
    initial_count = len(df_merged)

    df_merged = df_merged.drop_duplicates(subset=["Title"], keep="last")
    final_count = len(df_merged)

    if initial_count != final_count:
        print(f"🧹 Removed {initial_count - final_count} duplicate record(s).")

    # 4. Save enhanced CSV
    df_merged.to_csv(output_csv, index=False, encoding="utf-8-sig")
    print(f"💾 Exported CSV: {output_csv} ({final_count} total records)")

    # 5. Save enhanced JSON
    json_data = df_merged.to_dict(orient="records")
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)
    print(f"💾 Exported JSON: {output_json}")


if __name__ == "__main__":
    merge_datasets()