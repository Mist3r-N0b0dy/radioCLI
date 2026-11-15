"""Tests de la configuration des stations (genres, URLs, navigation)."""

from __future__ import annotations

import unittest

from radiocli.config import (
    DEFAULT_STATION,
    GENRES,
    GENRE_LABELS,
    STATIONS,
    Station,
    genre_label,
    next_station,
    station_by_key,
    stations_in_genre,
)


class StationTests(unittest.TestCase):
    def test_default_station_is_fip(self) -> None:
        station = station_by_key(DEFAULT_STATION)
        self.assertEqual(station.key, "fip")
        self.assertIn("radiofrance", station.url)
        self.assertEqual(station.bitrate, 128)
        self.assertEqual(station.genre, "eclectique")

    def test_keys_are_unique(self) -> None:
        keys = [station.key for station in STATIONS]
        self.assertEqual(len(keys), len(set(keys)))

    def test_all_stations_have_streams(self) -> None:
        for station in STATIONS:
            self.assertTrue(
                station.url.startswith(("http://", "https://")), station.url
            )
            self.assertGreater(station.bitrate, 0)
            self.assertTrue(station.name)
            self.assertTrue(station.subtitle)

    def test_all_genres_are_declared(self) -> None:
        declared = {genre for genre, _ in GENRES}
        for station in STATIONS:
            self.assertIn(station.genre, declared, station.key)
        # Chaque genre déclaré a au moins une station.
        for genre in declared:
            self.assertTrue(stations_in_genre(genre), genre)
        for genre, label in GENRES:
            self.assertEqual(genre_label(genre), label)
        self.assertEqual(genre_label("inconnu"), "inconnu")

    def test_french_public_rap_variete_present(self) -> None:
        keys = {station.key for station in STATIONS}
        for expected in (
            "franceinter", "franceinfo", "francemusique", "franceculture",
            "mouv", "skyrock", "generations",
            "mradio", "cheriefm", "rfm", "nostalgie", "icichanson",
            "fip", "fiprock", "fipjazz", "fipelectro", "fiphiphop",
            "fipworld", "fipgroove", "fippop", "fipreggae", "fipmetal",
            "nova",
        ):
            self.assertIn(expected, keys)

    def test_electro_house_techno_dnb_present(self) -> None:
        expected = {
            "fipelectro": "electro",
            "radiofg": "electro",
            "meuh": "electro",
            "fgdeep": "house",
            "fgchic": "house",
            "tldeep": "house",
            "pulsclub": "house",
            "fgunderground": "techno",
            "fgtechno": "techno",
            "tlminimal": "techno",
            "bassdrive": "dnb",
            "dnbradio": "dnb",
            "edmdnb": "dnb",
        }
        by_key = {station.key: station for station in STATIONS}
        for key, genre in expected.items():
            self.assertIn(key, by_key)
            self.assertEqual(by_key[key].genre, genre)
        self.assertEqual(len(STATIONS), 39)

    def test_lookup_is_tolerant(self) -> None:
        self.assertEqual(station_by_key(" FIP ").key, "fip")
        self.assertEqual(station_by_key("defaut").key, "fip")
        self.assertEqual(station_by_key("défaut").key, "fip")

    def test_unknown_station_raises(self) -> None:
        with self.assertRaises(KeyError):
            station_by_key("polka")

    def test_label_contains_bitrate(self) -> None:
        station = Station("x", "Test", "Sub", "http://example/x.mp3", 128)
        self.assertEqual(station.label(), "Test (128 kbps)")


class NavigationTests(unittest.TestCase):
    def test_meme_genre(self) -> None:
        station = station_by_key("fip")
        following = next_station(station, same_genre=True)
        self.assertEqual(following.genre, "eclectique")
        self.assertNotEqual(following.key, station.key)

    def test_boucle_du_genre(self) -> None:
        last = stations_in_genre("eclectique")[-1]
        first = stations_in_genre("eclectique")[0]
        self.assertEqual(next_station(last, same_genre=True).key, first.key)

    def test_genre_suivant(self) -> None:
        target = next_station(station_by_key("fipreggae"), same_genre=False)
        self.assertEqual(target.genre, "rock")
        self.assertEqual(target.key, "rock")

    def test_genre_suivant_boucle(self) -> None:
        last = STATIONS[-1]  # dernière station du dernier genre
        target = next_station(last, same_genre=False)
        self.assertEqual(target.genre, "reggae")
        self.assertEqual(target.key, "reggae")

    def test_ordre_des_genres(self) -> None:
        order = [genre for genre, _ in GENRES]
        current = STATIONS[0]
        seen = [current.genre]
        for _ in range(len(order) - 1):
            current = next_station(current, same_genre=False)
            seen.append(current.genre)
        self.assertEqual(seen, order)


if __name__ == "__main__":
    unittest.main()
