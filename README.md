# Ambulance coverage atlas

Explore the [public English and Hebrew map](https://shoval-c.github.io/ambulance-demand-atlas-r9/) for Greater London, Paris, Arlington (Texas), Sioux Falls (South Dakota), New York City and Tel Aviv–Yafo. Select any H3 resolution 9 cell for a modeled shared-ambulance count and weekday daytime, weekday night and Shabbat rankings.

Review [city standards and source checks](https://shoval-c.github.io/ambulance-demand-atlas-r9/audit/). To rerun the verification from a checkout, install Python dependencies with `python -m pip install h3==4.1.2 requests==2.34.1 pypdf==7.1.1` and run `python audit/verify.py`. The command writes a fresh results JSON and signals when a source needs review.

Download [all six city CSVs](https://shoval-c.github.io/ambulance-demand-atlas-r9/ambulance-atlas-csv.zip). The archive includes city-specific sources, measurement units and assumptions. Coverage areas overlap and their crew counts cannot be summed into a fleet size. Staffing estimates have not been validated against actual travel times or crew availability.
