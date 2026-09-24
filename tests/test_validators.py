"""Тесты валидаторов пользовательского ввода."""
from __future__ import annotations

import config
from domain.validators import (age_bucket, contact_url, validate_age, validate_bio, validate_city,
                               validate_games, validate_languages, validate_request_text)


def test_validate_age_boundaries():
    assert validate_age("22") == (True, 22)
    assert validate_age("17")[0] is False
    assert validate_age("abc")[0] is False
    assert validate_age(str(config.MAX_AGE + 5))[0] is False


def test_validate_city():
    assert validate_city("Омск") == (True, "Омск")
    assert validate_city("A")[0] is False
    assert validate_city("x" * (config.CITY_MAX + 1))[0] is False


def test_validate_bio_limits():
    assert validate_bio("Играю вечерами, ищу спокойную пати")[0] is True
    assert validate_bio("мало")[0] is False
    assert validate_bio("x" * (config.BIO_MAX + 1))[0] is False


def test_validate_games_splits_dedupes_and_limits():
    ok, games = validate_games("Valorant, Dota 2; valorant\nCS2")
    assert ok and games == ["Valorant", "Dota 2", "CS2"]
    assert validate_games("")[0] is False
    assert validate_games("a,b,c,d,e,f")[0] is False


def test_validate_languages():
    ok, langs = validate_languages(["ru", "ru", "en", "zz"])
    assert ok and langs == ["ru", "en"]
    assert validate_languages([])[0] is False
    assert validate_languages(["ru", "en", "de", "es", "tr"])[0] is False


def test_validate_request_text():
    assert validate_request_text("Привет! Ищу пати в Valorant")[0] is True
    assert validate_request_text("   ")[0] is False
    assert validate_request_text("x" * (config.REQUEST_TEXT_MAX + 1))[0] is False


def test_contact_url_prefers_username():
    assert contact_url("@frostbyte", 5) == "https://t.me/frostbyte"
    assert contact_url(None, 5) == "tg://user?id=5"


def test_age_bucket_labels():
    assert age_bucket(20) == "18-24"
    assert age_bucket(30) == "25-34"
    assert age_bucket(90) == "45+"
