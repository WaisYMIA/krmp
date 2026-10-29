Put the file  kenya-counties.geojson  in this folder.

Why: it makes the county map on the ROC page load instantly and work offline, with no dependence on an
outside server. The page already looks for  data/kenya-counties.geojson  first.

How to create it (one time): open  export-county-boundaries.html  (from the KRMP County Boundary Export Tool
package) in a browser with internet, press Start, check the preview looks like Kenya, press Download, then
save the file here.

If this file is absent the map still works: it falls back to a copy saved on the visitor's device, then to the
live Survey of Kenya services.
