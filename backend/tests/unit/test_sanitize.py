from campus_parking.ingestion.sanitize import REPLACEMENT, clean
from campus_parking.ingestion.service import capped_payload

NUL = chr(0)
LONE = chr(0xD800)


def test_replaces_nul_and_lone_surrogates_everywhere() -> None:
    dirty = {f"k{NUL}": [f"a{LONE}b", {f"x{LONE}": f"{NUL}"}, 1, None, True], "n": 2.5}
    assert clean(dirty) == {
        f"k{REPLACEMENT}": [f"a{REPLACEMENT}b", {f"x{REPLACEMENT}": REPLACEMENT}, 1, None, True],
        "n": 2.5,
    }


def test_leaves_valid_text_alone() -> None:
    text = "Estacionamento Principal çã \U0001f697"
    assert clean(text) == text
    assert clean([text, {"a": text}]) == [text, {"a": text}]


def test_result_is_encodable_and_nul_free() -> None:
    out = repr(clean({LONE: [f"{NUL}{LONE}"]}))
    assert NUL not in out
    clean(f"{LONE}").encode("utf-8")


def test_payload_is_cleaned_before_the_cap() -> None:
    assert capped_payload({"v": f"{NUL}{LONE}"}) == {"v": REPLACEMENT * 2}
    big = capped_payload({"v": LONE * 5000})
    assert big["truncated"] is True
    assert LONE not in big["preview"]
