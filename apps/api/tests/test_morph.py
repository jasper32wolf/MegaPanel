from app.services.morph import city_placeholders, inflect_cases, validate_agreement


def test_moscow_prep():
    forms = inflect_cases("Москва")
    assert forms["nom"] == "Москва"
    # pymorphy3: в Москве
    assert "москв" in forms["prep"].lower()
    ph = city_placeholders("Москва", forms)
    assert "{city_prep}" not in ph["city_prep"]
    assert ph["city_prep"]


def test_validate_agreement():
    issues = validate_agreement("в {city_prep}", {"city_prep": "Москве"})
    assert issues == []
    issues = validate_agreement("в {city_prep}", {})
    assert "missing:city_prep" in issues
