import json
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from diagnose_sources import decode_json  # noqa: E402
from province_geometry import classify, load_full, parse_aemet_dms  # noqa: E402


class Phase0Tests(unittest.TestCase):
    def test_iso_8859_1_and_null_vs_zero(self):
        self.assertEqual(decode_json(b'{"name":"\xc1vila"}')["name"], "Ávila")
        sample = json.loads((ROOT / "tests/fixtures/aemet_observacion_sintetica.json").read_text())[0]
        self.assertEqual(sample["ta"], 0)
        self.assertIsNone(sample["prec"])
        self.assertNotEqual(sample["pres"], sample["pres_nmar"])

    def test_dms(self):
        self.assertAlmostEqual(parse_aemet_dms("394924N"), 39 + 49 / 60 + 24 / 3600)
        self.assertAlmostEqual(parse_aemet_dms("025309W"), -(2 + 53 / 60 + 9 / 3600))
        with self.assertRaises(ValueError):
            parse_aemet_dms("406199N")

    def test_official_polygons_and_outside(self):
        collection = load_full(ROOT / "config/provinces-full.geojson.gz")
        for lon, lat, code in ((-3.7038, 40.4168, "28"), (-4.7009, 40.6564, "05"),
                               (-4.1184, 40.9429, "40"), (-3.164, 40.633, "19")):
            self.assertEqual(classify(lon, lat, collection), code)
        self.assertIsNone(classify(2.17, 41.39, collection))

    def test_shared_boundary_is_deterministic(self):
        def square(x1, x2, code):
            return {"type": "Feature", "properties": {"province_code": code}, "geometry": {
                "type": "Polygon", "coordinates": [[[x1, 0], [x2, 0], [x2, 1], [x1, 1], [x1, 0]]]}}
        collection = {"features": [square(0, 1, "28"), square(1, 2, "05")]}
        self.assertEqual(classify(1, 0.5, collection), "05")

    def test_synthetic_xml_station_time_and_rain_counter(self):
        root = ET.parse(ROOT / "tests/fixtures/meteoclimatic_sintetico.xml").getroot()
        station = root.find("./stations/station")
        self.assertNotEqual(root.findtext("pubDate"), station.findtext("pubDate"))
        self.assertEqual(station.findtext("./stationdata/rain/total"), "0.0")
        self.assertIsNone(station.find("./stationdata/rain/now"))


if __name__ == "__main__":
    unittest.main()
