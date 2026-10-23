# MANIFEST fieldcheck2

Downloads under src/<name>/ (gitignored). Nothing executed. sha256 full in table; fetched 2026-10-23.

| Dataset | URL | Licence |
|---|---|---|
| A Ty Ty pond | https://api.figshare.com/v2/articles/26142568 (files via ndownloader.figshare.com/files/<id>; DOI 10.15482/USDA.ADC/26142568.v1) | CC BY 4.0 |
| B TN pan evap | https://nwdp.nwic.gov.in/dataset/e5c186ec-acec-4839-99f2-10c66cfb6708/resource/2a17245c-29f4-4b02-bb34-605d6a76d097/download/pan_evaporation_man_twice_a_day_1972_2026.csv (95 MB) | not stated on page (License heading commented out); treat as unknown, likely NDSAP/GODL |
| C FWI sheet | https://docs.google.com/spreadsheets/d/1EpIWkIlgTrq2M0RmMYEM2g83d7-SOSt0_YKgK6m2zAA/export?format=csv ; repo github.com/fish-welfare-initiative/fwi-farm-data-india (+ docs/data_dictionary.md) | sheet: none stated; farm-data repo CC-BY-4.0; Data-Campaign-Data repo: no licence |
| D WorldFish Vietnam | doi:10.7910/DVN/FUCDMC (2018), doi:10.7910/DVN/VJEDSU (2019); metadata via dataverse.harvard.edu/api/datasets/:persistentId | CC BY 4.0, but files access-gated (guestbook) |
| E Guppy | https://data.mendeley.com/public-api/datasets/xnpj6353yw (v1, DOI 10.17632/xnpj6353yw.1); files via public-files/.../file_downloaded (302 to S3) | CC BY 4.0 |

## sha256

```
2c09ef9b82b3a95c415ced41b9aca031f96d4eda3e71a234bf27bf9cae14014b  src/A_tyty/9_TyTyCooperatorFarm_Data_Dictionary_Pond_Study_2022.xlsx
88dc67ff653e490a71e4d5330464180323621c56bd4a112f9298d130c83726f4  src/A_tyty/Table4_TyTyCooperatorFarm_Bathymetry_Survey_20220923_20220927.csv
a2cbedb133c4b5470e728760f68ebcf7c9308c5c6481b544b3269e44b06525a4  src/A_tyty/Table5_TyTyCooperatorFarm_InOut_Pumping_Data_2022.csv
d922102e71b246a975b3c9b533f3aceac8632dbcca8015f560bde2be42cf41dc  src/A_tyty/Table6_TyTyCooperatorFarm_Shoreline_Survey_20220922.csv
deb1554ddbef0b8028af6518cd7edddbf0ac231e41e9fb9401fc91687ba92837  src/A_tyty/Table7_TyTyCooperatorFarm_Staff_Gauge_Pressure_Readings_2021_2023.csv
c40d03f8d3e85a4ea5b01daa77b4eb42ebf13252820f9afd89c5a6457555533d  src/A_tyty/Table8_TyTyCooperatorFarm_Total_Precipitation_Rainfall_2022.csv
dd1b847888367174cd50bb71ff8d1bb4ac41c9d855fbac4739e7cc227226fb7e  src/A_tyty/meta.json
2d944d59823654ac742a4080aeeab40a214605719e84f657cfa6536c2c889c0c  src/B_tn/page.html
a2786fc85182351047ce7f9d2436b88aaf229dfae4375eb35700005847e14cb0  src/B_tn/pan.csv
179355c41608f22eea8f7c21a2c080224f99a1c6a0db25b6d5bcba751916f48b  src/C_fwi/dd.md
10aa328d63fb31c4f701ef3d4a840c39c9873f7f329b46441c4b46837170d6a1  src/C_fwi/farm_readme.md
57e75aadc37c0b82296b29daf5fb0dc8dabe8e257c791010c811856b2a4c0b62  src/C_fwi/sheet.csv
2f7aad859faac01be336906b9f6f7c4c9976eb47299ef7405f8888c30149b34e  src/E_guppy/fig1.xlsx
85c9cc8c95fcd185d36ec8eacebca7be7a87eff54e090fb223851ee501a3eee7  src/E_guppy/fig2.xlsx
feb164005d12d94c5498bf368919e1a94d79874fd2266924611abf70fca0b08c  src/E_guppy/files.json
d694901d084227b888c2d37ed6e16c225021d4df57b27b261bc46bc42ca2c550  src/E_guppy/tab1.xlsx
0464ab008259be9e2068107924926afc228bb27702ec8ddf56bb13371f53129b  src/D_wf/FUCDMC.json
356754a01cbcfa1ff26798b4024087b24cb4a6abd6a00e7e91b848ece19cd978  src/D_wf/VJEDSU.json
```

Figshare skipped (not needed): 1_..RGB_mosaic.tif 479 MB, 2_..DTM.tif 17 MB, 3_..Surface_model.tif 51 MB.
D files not fetched (access-gated): Input_output_data_2018/2019.xlsx, Input_water_daily_monitoring_2018.xlsx, Input_water_daily_2019.xlsx, Water_growth_weekly_2018/2019.xlsx, protocols.
Table4/5/7/8 md5 match figshare supplied_md5 (verified).
