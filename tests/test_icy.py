"""Tests du parseur de métadonnées ICY (hors réseau)."""

from __future__ import annotations

import unittest

from radiocli.icy import IcyMetadata, _decode_text


class ParseMetaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.meta = IcyMetadata("http://example.invalid/stream.mp3")

    def test_parses_title(self) -> None:
        self.meta._parse_meta(b"StreamTitle='Sai Sai - Revolt Dub';StreamUrl='';")
        self.assertEqual(self.meta.title, "Sai Sai - Revolt Dub")

    def test_ignores_empty_metadata(self) -> None:
        self.meta._parse_meta(b"\x00\x00\x00")
        self.assertEqual(self.meta.title, "")

    def test_ignores_empty_stream_title(self) -> None:
        self.meta._parse_meta(b"StreamTitle='';")
        self.assertEqual(self.meta.title, "")

    def test_keeps_apostrophe_inside_title(self) -> None:
        self.meta._parse_meta(b"StreamTitle='Lee \"Scratch\" Perry - Dub';")
        self.assertEqual(self.meta.title, 'Lee "Scratch" Perry - Dub')

    def test_latin1_title(self) -> None:
        raw = "StreamTitle='Été - Soleil';".encode("latin-1")
        self.meta._parse_meta(raw)
        self.assertEqual(self.meta.title, "Été - Soleil")

    def test_title_setter(self) -> None:
        self.meta.title = "Toto - Africa"
        self.assertEqual(self.meta.title, "Toto - Africa")


class DecodeTextTests(unittest.TestCase):
    def test_utf8(self) -> None:
        self.assertEqual(_decode_text("Ça va".encode()), "Ça va")

    def test_fallback_latin1(self) -> None:
        self.assertEqual(_decode_text(b"\xffabc"), "ÿabc")


if __name__ == "__main__":
    unittest.main()
