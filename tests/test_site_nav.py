#!/usr/bin/env python3
"""test_site_nav.py — navegación cruzada del sitio publicado.

Guard de regressiones de navegación (los dashboards ddl y context
quedaron deployados sin enlace desde el index, y ninguna página
publicada tenía vuelta al índice):

- todo dashboard HTML publicado en data/ debe estar enlazado desde
  index.html (sin páginas huérfanas: si se agrega un dashboard y no se
  suma al índice, el test roja)
- toda página publicada en data/ debe tener back-link a ../index.html
  (los generadores lo insertan vía site_theme.back_link())
"""

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def published_pages():
    return sorted((REPO / "data").glob("*.html"))


class TestBackLinks(unittest.TestCase):
    def test_every_published_page_links_back_to_index(self):
        pages = published_pages()
        self.assertTrue(pages, "no hay páginas HTML en data/ (regenerar)")
        for page in pages:
            with self.subTest(page=page.name):
                html = page.read_text(encoding="utf-8")
                self.assertIn('href="../index.html"', html,
                              f"{page.name} sin back-link al índice")

    def test_back_link_is_first_focusable_element(self):
        """El back-link precede al resto del contenido visible (el skip
        link de fpa/guía puede ir antes; el requisito es que aparezca en
        el header/banner, no al final de la página)."""
        for page in published_pages():
            with self.subTest(page=page.name):
                html = page.read_text(encoding="utf-8")
                first_body_chunk = html.split("<body", 1)[1]
                self.assertLess(first_body_chunk.index('href="../index.html"'),
                                first_body_chunk.index("</body>"))

    def test_back_link_does_not_point_to_data_data(self):
        """coffe-ulw: los targets relativos mal armados daban 404
        (data/data). El back-link correcto es ../index.html, nunca
        data/index.html ni data/data/index.html."""
        for page in published_pages():
            with self.subTest(page=page.name):
                html = page.read_text(encoding="utf-8")
                self.assertNotIn('href="data/index.html"', html)
                self.assertNotIn('href="data/data/', html)


class TestIndexCoverage(unittest.TestCase):
    def test_index_links_every_data_html(self):
        index = (REPO / "index.html").read_text(encoding="utf-8")
        pages = published_pages()
        self.assertTrue(pages)
        for page in pages:
            with self.subTest(page=page.name):
                self.assertIn(f'href="data/{page.name}"', index,
                              f"{page.name} huérfano: no está enlazado "
                              "desde index.html")


if __name__ == "__main__":
    unittest.main()
