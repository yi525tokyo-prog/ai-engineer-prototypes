"""Seed knowledge: entry points a well-informed local would name, and official guidance pages.

These are *starting points* registered in the source registry with ``origin=pack:<CC>``;
they carry no URL templates or candidate data -- every listing is reached by navigation at
run time, and a seed that turns out blocked or useless is demoted like any discovered
source. Countries not listed here have no seeds at all: their sources come from the
global aggregators and from discovery (see ``regent/acquisition/discovery.py``).
"""

from __future__ import annotations

from regent.acquisition.discovery import DomainVocabulary
from regent.acquisition.types import SourceSpec

GLOBAL_SOURCES = [
    SourceSpec("housinganywhere.com", "aggregator", "https://housinganywhere.com/", nav=["{city}"],
               purpose="mid/long-term rentals in many cities"),
    SourceSpec("www.spotahome.com", "aggregator", "https://www.spotahome.com/", nav=["{city}"],
               purpose="mid-term furnished rentals"),
    SourceSpec("www.craigslist.org", "aggregator", "https://www.craigslist.org/about/sites",
               nav=["{city}", "apts / housing"], purpose="classifieds (Americas, some other cities)"),
    SourceSpec("www.hostelworld.com", "hostel", "https://www.hostelworld.com/", nav=["{country_name}", "{city}"],
               purpose="hostel beds: the no-commitment option"),
]

COUNTRY_SEEDS: dict[str, list[SourceSpec]] = {
    "GB": [SourceSpec("www.rightmove.co.uk", "portal", "https://www.rightmove.co.uk/", nav=["{city}"]),
           SourceSpec("www.openrent.co.uk", "portal", "https://www.openrent.co.uk/", nav=["{city}"]),
           SourceSpec("www.onthemarket.com", "portal", "https://www.onthemarket.com/", nav=["{city}"]),
           SourceSpec("www.spareroom.co.uk", "portal", "https://www.spareroom.co.uk/", nav=["{city}"])],
    "US": [SourceSpec("www.rent.com", "portal", "https://www.rent.com/", nav=["{city}"]),
           SourceSpec("www.zillow.com", "portal", "https://www.zillow.com/", nav=["{city}"])],
    "DE": [SourceSpec("www.wg-gesucht.de", "portal", "https://www.wg-gesucht.de/", nav=["{city}"]),
           SourceSpec("www.kleinanzeigen.de", "portal", "https://www.kleinanzeigen.de/", nav=["{city}"]),
           SourceSpec("www.immowelt.de", "portal", "https://www.immowelt.de/", nav=["{city}"])],
    "NZ": [SourceSpec("www.realestate.co.nz", "portal", "https://www.realestate.co.nz/", nav=["{city}"])],
}

AUTHORITY: dict[str, list[str]] = {
    "GB": ["https://www.gov.uk/check-uk-visa", "https://www.gov.uk/private-renting"],
    "NZ": ["https://www.immigration.govt.nz/new-zealand-visas"],
    "DE": ["https://www.make-it-in-germany.com/en/visa-residence"],
    "US": ["https://travel.state.gov/content/travel/en/us-visas.html"],
    "JP": ["https://www.isa.go.jp/en/applications/procedures/index.html"],
    "IE": ["https://www.irishimmigration.ie/"],
    "ES": ["https://www.exteriores.gob.es/en/ServiciosAlCiudadano/Paginas/Visados.aspx"],
    "PT": ["https://vistos.mne.gov.pt/en/"],
    "NL": ["https://ind.nl/en"],
}

COUNTRY_NAMES = {"GB": "United Kingdom", "US": "United States", "DE": "Germany", "NZ": "New Zealand",
                 "ES": "Spain", "PT": "Portugal", "IT": "Italy", "FR": "France", "NL": "Netherlands",
                 "IE": "Ireland", "AT": "Austria", "BE": "Belgium", "CZ": "Czech Republic", "HU": "Hungary",
                 "GR": "Greece", "PL": "Poland", "JP": "Japan", "AU": "Australia", "CA": "Canada",
                 "DK": "Denmark", "SE": "Sweden", "CH": "Switzerland", "LT": "Lithuania", "LV": "Latvia",
                 "EE": "Estonia", "SI": "Slovenia", "MT": "Malta", "CY": "Cyprus", "LU": "Luxembourg",
                 "TR": "Turkey", "AE": "United Arab Emirates", "TH": "Thailand", "SG": "Singapore"}


def housing_vocabulary(verify, city_share=None) -> DomainVocabulary:
    return DomainVocabulary(
        nav_words={
            "en": ["to rent", "for rent", "property to rent", "rentals", "rent", "apartments", "lettings", "flats",
                   "rooms"],
            "de": ["mieten", "wohnung mieten", "wohnungen", "zur miete", "wg-zimmer"],
            "es": ["alquiler", "alquilar", "pisos en alquiler"], "fr": ["location", "louer", "appartements"],
            "it": ["affitto", "affitti", "case in affitto"], "nl": ["huur", "huren", "huurwoningen"],
            "pt": ["arrendar", "arrendamento", "alugar"], "ja": ["賃貸"],
        },
        avoid_words=["for-sale", "property-for-sale", "/sale", "/buy", "kaufen", "/kauf", "comprar", "venta", "vente",
                     "acheter", "vendita", "koop", "new-homes", "obra-nueva", "commercial", "gewerbe",
                     "/residential/sale", "/sold", "/auction", "vacation", "cat=vac", "holiday", "ferien", "urlaub",
                     "short-stay", "/hotels"],
        stems={
            "en": ["rent", "rentals", "lettings", "flats", "rooms", "property", "homes", "realestate", "apartments"],
            "de": ["wohnung", "wohnungen", "immobilien", "mieten", "wohnungsboerse", "immo"],
            "es": ["pisos", "alquiler", "inmobiliaria", "casas"], "fr": ["location", "immobilier", "logement"],
            "it": ["immobiliare", "casa", "affitto"], "nl": ["huurwoningen", "woningen", "huren", "kamernet"],
            "pt": ["imoveis", "casa", "arrendamento"],
        },
        verify=verify, kind="portal", city_share=city_share)
