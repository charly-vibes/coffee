"""Módulo extraído de viz-fpa.py (decomposición, comportamiento idéntico)."""
import calendar  # noqa: F401
import html  # noqa: F401
import json  # noqa: F401
import re  # noqa: F401
import sys
from datetime import date, datetime  # noqa: F401
from html.parser import HTMLParser  # noqa: F401
from pathlib import Path  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fpa_config  # noqa: F401,E402
import schema_validate  # noqa: F401,E402
import site_theme  # noqa: F401,E402




FPA_REF_RE = re.compile(r"FPA-\d{3}[a-z]?(?:\s*[/…]\s*\d{3}[a-z]?)*")

VOID_ELEMENTS = {"area", "base", "br", "col", "embed", "hr", "img",
                 "input", "link", "meta", "param", "source", "track",
                 "wbr"}


class _FpaRefMover(HTMLParser):
    """Mueve refs "FPA-xxx" del texto visible al atributo title del
    elemento contenedor (machine-readable, invisible). Los chips de
    marginalia quedan intactos (anti-goal del ticket: sí llevan ids)."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out = []
        self.stack = []  # índices de tokens de start tag abiertos

    def _marginalia(self, attrs):
        return "marginalia" in dict(attrs).get("class", "").split()

    def handle_starttag(self, tag, attrs):
        idx = len(self.out)
        self.out.append({"tag": tag, "attrs": list(attrs),
                         "marg": self._marginalia(attrs)})
        if tag not in VOID_ELEMENTS:
            self.stack.append(idx)

    def handle_startendtag(self, tag, attrs):
        # conservar el autocierre: sin "/>" el parser HTML anida elementos
        # SVG (rects hijos de rect no se pintan → waterfall vacío, F7).
        self.out.append(self._serialize(tag, list(attrs), [], self_closing=True))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.out[self.stack[i]]["tag"] == tag:
                del self.stack[i:]
                break
        self.out.append(f"</{tag}>")

    def _in_marginalia(self):
        return any(self.out[i]["marg"] for i in self.stack)

    @staticmethod
    def _serialize(tag, attrs, extra_ids, self_closing=False):
        if extra_ids:
            attrs = list(attrs)
            merged = None
            for i, (k, v) in enumerate(attrs):
                if k == "title":
                    merged = (v + " · " if v else "") + " ".join(extra_ids)
                    attrs[i] = (k, merged)
                    break
            if merged is None:
                attrs.append(("title", " ".join(extra_ids)))
        parts = [f"<{tag}"]
        for k, v in attrs:
            if v is None:
                parts.append(f" {k}")
            else:
                parts.append(f' {k}="{html.escape(v, quote=True)}"')
        parts.append("/>" if self_closing else ">")
        return "".join(parts)

    def handle_data(self, data):
        if not FPA_REF_RE.search(data) or self._in_marginalia():
            self.out.append(data)
            return
        ids = [m.group(0).replace(" ", "") for m in FPA_REF_RE.finditer(data)]
        t = re.sub(r"config\s+(?=FPA-\d{3})", "", data)
        t = FPA_REF_RE.sub("", t)
        t = re.sub(r"\(\s*\)", "", t)          # paréntesis vacío
        t = re.sub(r"\(\s*[,;:]\s*", "(", t)   # puntuación huérfana inicial
        t = re.sub(r"\s*[,;:]\s*\)", ")", t)   # puntuación huérfana final
        t = re.sub(r"\s+\)", ")", t)
        t = re.sub(r"\(\s+", "(", t)
        t = re.sub(r"[ \t]{2,}", " ", t)        # colapso de espacios
        t = re.sub(r" +([.,;:!?])", r"\1", t)   # espacio antes de puntuación
        self.out.append(t)
        if self.stack:
            self.out[self.stack[-1]].setdefault("refs", []).extend(ids)

    def handle_entityref(self, name):
        self.out.append(f"&{name};")

    def handle_charref(self, name):
        self.out.append(f"&#{name};")

    def handle_comment(self, data):
        self.out.append(f"<!--{data}-->")

    def handle_decl(self, decl):
        self.out.append(f"<!{data}>")

    def result(self):
        chunks = []
        for tok in self.out:
            if isinstance(tok, str):
                chunks.append(tok)
            else:
                chunks.append(self._serialize(tok["tag"], tok["attrs"],
                                              tok.get("refs", [])))
        return "".join(chunks)


def move_fpa_ids_to_titles(html_text):
    """coffe-6lz F4: saca "FPA-xxx" del texto visible y lo pasa al title
    del elemento contenedor. Determinista; marginalia sin tocar."""
    mover = _FpaRefMover()
    mover.feed(html_text)
    mover.close()
    return mover.result()


def fig(value_html, provenance, extra_cls=""):

    """Envoltorio de cifra con provenance tag (FPA-003)."""
    cls = f' class="fig{"" if not extra_cls else " " + extra_cls}"'
    return f'<span{cls} data-provenance="{provenance}">{value_html}</span>'


def claim_html(c):
    """FPA-009: claim con métrica y threshold visibles (con provenance)."""
    return (f'<p class="claim">{c["verdict"]} <strong>{c["text"]}</strong> '
            f'— métrica: <em>{c["metric_name"]}</em> '
            + fig(fmt_usd(c["metric_value"]), "reported") + prov_tag("reported")
            + ' · umbral: '
            + fig(fmt_usd(c["threshold"]), c["threshold_provenance"])
            + prov_tag(c["threshold_provenance"]) + '</p>')
from viz_fpa_common import _link  # noqa: E402

_link(globals())
