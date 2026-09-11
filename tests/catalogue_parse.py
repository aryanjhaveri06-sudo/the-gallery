"""The catalogue-text parsers shared by Pundole's, Christie's and Bonhams.

    python3 tests/catalogue_parse.py

Each case is a shape one of the three houses actually prints. The two traps
worth remembering: fractional inches ("18 7/8") must not read as "8", and a
two-digit year must follow an apostrophe or the dimensions read as 1972.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingest"))
from common import split_artist, parse_description, year_from        # noqa: E402
from bonhams import split_styled                                       # noqa: E402

CASES = [
    # split_artist: (raw) -> (name, years)
    (split_artist, "JAMINI ROY (1887-1972)", ("Jamini Roy", "1887–1972")),
    (split_artist, "SAYED HAIDER RAZA (1922-2016)", ("Sayed Haider Raza", "1922–2016")),
    (split_artist, "SUBODH GUPTA (B. 1964)", ("Subodh Gupta", "b. 1964")),
    (split_artist, "Ahmad Morshedloo (Iran, born 1973)", ("Ahmad Morshedloo", "b. 1973")),
    (split_artist, "Tayeba Begum Lipi (B.1969)", ("Tayeba Begum Lipi", "b. 1969")),
    (split_artist, "Unknown Artist", ("Unknown Artist", None)),
    # parse_description: -> (medium, size)
    (parse_description,
     "SAYED HAIDER RAZA (1922-2016) <BR>\nBlack Sun <BR>\nsigned and dated 'S.H. RAZA, '53.' <BR>\n"
     "gouache on paper <BR>\n18 ¼ x 18 7⁄8 in. (46.4 x 47.9 cm.) <BR>\nExecuted in 1953",
     ("gouache on paper", "18.3 x 18.9 in")),
    (parse_description,
     '<div class="firstLine">Nandalal Bose</div><div class="otherLine">Untitled</div>'
     '<div class="otherLine">ink on rice paper, framed<br/>36.6   x 46.8cm   (14 7/16   x 18 7/16in).</div>',
     ("ink on rice paper, framed", "14.4 x 18.4 in")),
    (parse_description, "pastels on paper<br>50.8 x 76.2cm (20 x 30in).", ("pastels on paper", "20.0 x 30.0 in")),
    (parse_description, "56 1/2 x 20 1/2 in.", (None, "56.5 x 20.5 in")),
    # year_from
    (year_from, "Executed in 1953", "1953"),
    (year_from, "Painted  circa  1882", "1882"),
    (year_from, "signed and dated 'Sabavala '66' lower right", "1966"),
    (year_from, "signed and dated 'Husain' lower right; acrylic on canvas; 72.9 x 91.7cm", None),
    (year_from, "oil on canvas", None),
    # split_styled (Bonhams): -> (artist_raw, title)
    (split_styled,
     '<div class="firstLine">Nandalal Bose</div><div class="secondLine">(1882-1966)</div>'
     '<div class="otherLine"><i>Untitled (Four Trees)</i></div>',
     ("Nandalal Bose (1882-1966)", "Untitled (Four Trees)")),
    (split_styled, '<div class="firstLine">Unknown Artist</div><div class="otherLine">Untitled</div>',
     ("Unknown Artist", "Untitled")),
]


def main():
    fails = [(f.__name__, arg, want, got) for f, arg, want in CASES
             if (got := f(arg)) != want]
    for name, arg, want, got in fails:
        print(f"FAIL {name}({arg!r})\n     want {want!r}\n     got  {got!r}")
    print(f"{len(CASES) - len(fails)}/{len(CASES)} catalogue parse cases correct")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
