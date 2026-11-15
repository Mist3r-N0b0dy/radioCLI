"""Tests du menu modal et du filtre de stations (sans curses)."""

from __future__ import annotations

import unittest

from radiocli.config import GENRES, STATIONS, genre_label
from radiocli.menu import (
    MAIN_ITEMS,
    PAGE_MAIN,
    PAGE_STATIONS,
    MenuState,
    filter_stations,
    normalize,
    station_matches,
)


class FilterTests(unittest.TestCase):
    def test_normalize(self) -> None:
        self.assertEqual(normalize("Générations"), "generations")
        self.assertEqual(normalize("Variété FR"), "variete fr")
        self.assertEqual(normalize(None), "")

    def test_filtre_vide_retourne_tout(self) -> None:
        self.assertEqual(filter_stations(""), STATIONS)
        self.assertEqual(filter_stations("   "), STATIONS)

    def test_filtre_par_nom_genre_et_sans_accent(self) -> None:
        keys = {station.key for station in filter_stations("fip")}
        self.assertIn("fipjazz", keys)
        self.assertIn("fiprock", keys)
        rap = {station.key for station in filter_stations("rap")}
        self.assertEqual(rap, {"mouv", "skyrock", "generations"})
        self.assertEqual(
            [station.key for station in filter_stations("generation")],
            ["generations"],
        )
        variete = {station.key for station in filter_stations("variete")}
        self.assertIn("mradio", variete)
        self.assertIn("icichanson", variete)
        france = {station.key for station in filter_stations("france")}
        self.assertEqual(
            france, {"franceinter", "franceinfo", "francemusique", "franceculture"}
        )

    def test_filtre_multi_mots(self) -> None:
        result = filter_stations("fip rock")
        self.assertIn("fiprock", [station.key for station in result])
        self.assertTrue(all(station.genre == "rock" for station in result))
        self.assertEqual(filter_stations("zzz"), ())

    def test_station_matches_cle(self) -> None:
        station = next(s for s in STATIONS if s.key == "fipjazz")
        self.assertTrue(station_matches(station, "fipjazz"))
        self.assertTrue(station_matches(station, "jazz"))
        self.assertFalse(station_matches(station, "reggae"))


class MenuStateTests(unittest.TestCase):
    def test_ouverture_fermeture(self) -> None:
        menu = MenuState()
        self.assertFalse(menu.open)
        menu.open_main()
        self.assertTrue(menu.open)
        self.assertEqual(menu.page, PAGE_MAIN)
        menu.open_stations()
        self.assertEqual(menu.page, PAGE_STATIONS)
        menu.back()  # Échap : retour au menu principal
        self.assertEqual(menu.page, PAGE_MAIN)
        self.assertTrue(menu.open)
        menu.back()
        self.assertFalse(menu.open)

    def test_fermeture_reinitialise(self) -> None:
        menu = MenuState()
        menu.open_stations()
        menu.append_char("f")
        menu.selected = 3
        menu.close()
        self.assertEqual(menu.query, "")
        self.assertEqual(menu.selected, 0)

    def test_filtre_et_selection(self) -> None:
        menu = MenuState()
        menu.open_stations()
        self.assertEqual(len(menu.filtered()), len(STATIONS))
        menu.append_char("s")
        menu.append_char("k")
        menu.append_char("y")
        self.assertEqual([s.key for s in menu.filtered()], ["skyrock"])
        menu.backspace()
        self.assertEqual(menu.query, "sk")
        self.assertEqual(menu.selected, 0)

    def test_select_navigation_bornee_et_bouclee(self) -> None:
        menu = MenuState()
        menu.open_stations()
        total = len(menu.filtered())
        self.assertEqual(menu.current_station(), STATIONS[0])
        menu.move(-1)
        self.assertEqual(menu.current_station(), STATIONS[-1])
        menu.move(1)
        self.assertEqual(menu.current_station(), STATIONS[0])
        menu.move(total * 2 + 3)
        self.assertEqual(menu.current_station(), STATIONS[3])

    def test_filtre_limite_la_selection(self) -> None:
        menu = MenuState()
        menu.open_stations()
        menu.selected = 20
        for char in "skyrock":
            menu.append_char(char)
        self.assertEqual(menu.current_station().key, "skyrock")
        menu.ensure_selection()

    def test_aucune_station(self) -> None:
        menu = MenuState()
        menu.open_stations()
        for char in "zzzz":
            menu.append_char(char)
        self.assertEqual(menu.filtered(), ())
        self.assertIsNone(menu.current_station())
        menu.move(1)
        menu.ensure_selection()
        self.assertEqual(menu.selected, 0)

    def test_menu_principal_contient_auto_et_reactif(self) -> None:
        keys = [item[0] for item in MAIN_ITEMS]
        self.assertIn("a", keys)
        self.assertIn("c", keys)
        menu = MenuState()
        self.assertFalse(menu.auto_visual)
        self.assertFalse(menu.reactive)
        menu.auto_visual = True
        menu.reactive = True
        self.assertTrue(menu.auto_visual)
        self.assertTrue(menu.reactive)

    def test_lignes_groupees_par_genre(self) -> None:
        menu = MenuState()
        menu.open_stations()
        rows = menu.rows()
        headers = [row.label for row in rows if row.kind == "header"]
        self.assertEqual(len(headers), len(GENRES))
        station_rows = [row for row in rows if row.kind == "station"]
        self.assertEqual(len(station_rows), len(STATIONS))
        self.assertEqual(
            headers,
            [genre_label(genre).upper() for genre, _ in GENRES],
        )

    def test_index_ligne_selectionnee(self) -> None:
        menu = MenuState()
        menu.open_stations()
        menu.selected = 5
        index = menu.selected_row_index()
        self.assertEqual(menu.rows()[index].station, menu.current_station())


if __name__ == "__main__":
    unittest.main()
