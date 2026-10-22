# fieldcheck MANIFEST - 2026-10-22 (seed 20261022 unused; no sampling)

Branch research/hypoxia-external-validation, base commit 8eb7077. Downloaded data is in gitignored `src/`, not committed. Scripts in this folder are mine; no downloaded code was run (`python -X utf8 -I`, stdlib only, paths as args). Engine imported read-only from `OutdoorKoiFYP_research/Backend` only in `pan_plaus.py`.

## Sources
- PAN: https://api.figshare.com/v2/articles/10329347 (v2 = no files) and /versions/1 -> https://ndownloader.figshare.com/files/18810497 (v1, CC BY 4.0, md5 87feecefa93fb930b1f63751103812f3)
- FAB: https://zenodo.org/api/records/15669103 (concept); files from https://zenodo.org/api/records/15669104/files/<name>/content (CC BY 4.0; the 15669103 file URLs 404)
- MESO: https://zenodo.org/api/records/10520248/files/<name>/content (CC BY 4.0, v1)
- BRU: https://zenodo.org/api/records/11103557/files/<name>/content (CC BY 4.0)
- MESO zip extracted to src/meso/z/ (not executed). Nothing over 50 MB downloaded. Zenodo md5s matched for every file.

## SHA-256 (under src/)
    e9b5ed77a6ee007f2d83b7acd095fefea3730b9de119042958ed88e594c411d8 *pan/pan_v1.xlsx
    db5a4207bcd73ca71723ff52547131473d854e0127be42098edbe19ea172758f *pan/_meta.json
    610d7872d701b6cc2c25a01b4272b18eacaf5d10e2f40ca121c733426624467d *pan/_meta_v1.json
    ae296d9540d20980a29c82506e83a2193260163ca7ebdec9d26164c5fedd7188 *fab/FABLIGHT_FGA_area.csv
    f5a071693bce6f992d896429e2b06fa58465a18612de35c69e8715008360a6d5 *fab/FABLIGHT_FGA_final_biomass.csv
    23678add13a6bedb557cadd65c61f44c006ed4a7ba3d65e661485e00ebe8bd92 *fab/FABLIGHT_limnotron_chlorophyll_a.csv
    bf280184f0c85dd1e147df77d6f051eab2e69ace3e319f51baf93c7d52594c7c *fab/FABLIGHT_limnotron_nutrients.csv
    d3a038accf7003bed313b1ba8ba0464da18807b9d11ba717eb42a7218fb037ac *fab/FABLIGHT_limnotron_oxygen.csv
    473e7f730237a5900ccd808c5a1706ff49d300b9897120035ca2d781878afc9d *meso/LemCP_metaData_v1.xlsx
    de47f2a2493bc28dfd1ad2fd5b390e8b40060ad080d9dd8925555d723936c695 *meso/LemCP_DataORE_v1.zip
    81d2e401199cc4deaeaa014d7591945e45f6389b372f13e345f26e61e0a70f6c *bru/DATA -_Ebullitive.xlsx
    af4068cd33517232fa2eb8125ce55b70006092767f728b6af2f9a4e5527307bc *bru/DATA _Dissolved.xlsx
    a7ae71e8235db0f190ab623d9d964818a6af05d760d5fa320decd785662277aa *bru/DATA _Perturbed_sediments.xlsx
    afac9f65c24e2fcaedd4e540ed7184f4e43ed026295b8a1e5e8a8a8f11d9d86f *zen_10520248.json
    823d4fc91d9211c91fba2e326b616d5558881db7dcc75e0cd149a829357c16bc *zen_11103557.json
    2632e116464b35f20d6a6a83deb0bfb9e54f5821b3d997fa6f4480bcd8608d57 *zen_15669103.json

## Scripts
`read_xlsx.py` (single-sheet xlsx -> json), `xlsx_sheets.py` (multi-sheet dump), `pan_inspect.py`, `pan_plaus.py`.
Reproduce: `python -X utf8 -I fieldcheck/read_xlsx.py src/pan/pan_v1.xlsx src/pan/rows.json`, then `pan_inspect.py src/pan/rows.json` and `pan_plaus.py src/pan/rows.json`.
