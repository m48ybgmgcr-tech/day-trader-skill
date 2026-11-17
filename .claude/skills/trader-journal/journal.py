#!/usr/bin/env python3
"""Trading-Tagebuch (trader-journal): Ausfuehrungen in Euro, gespeichert in einem
PRIVATEN Git-Repository. Nur Standardbibliothek, Python 3.8+.

Dieses Werkzeug platziert keine Orders und hat keine Brokeranbindung. Es
protokolliert nur Käufe und Verkäufe, die der Nutzer selbst (z. B. in
Scalable Capital) ausgeführt und ausdruecklich gemeldet hat.

    python3 journal.py start                      # SessionStart-Hook: Tagebuch laden
    python3 journal.py status                     # geladen? Kapital, offene Positionen
    python3 journal.py kauf --strategie kurzfristig --symbol AAPL --stueck 1 \\
        --kurs 318,18 --gebuehren 0,99 --stop 315,00 --ziel 324,55
    python3 journal.py verkauf --strategie kurzfristig --symbol AAPL --stueck 1 \\
        --kurs 324,55 --gebuehren 0,99
    python3 journal.py storno --id ID --grund "Tippfehler"
    python3 journal.py bericht [--strategie kurzfristig|spekulativ] [--kurs AAPL=320,10]
    python3 journal.py liste [--anzahl 20]
    python3 journal.py pruefen
    python3 journal.py sichern [--hook]           # Stop-Hook: Ungesichertes pushen
    python3 journal.py init                       # leeres privates Repository einrichten

Speichern: Jede Eintragung wird sofort committet und gepusht (ohne Force-Push).
"Gespeichert" wird erst gemeldet, wenn GitHub den Commit bestätigt hat.
Gleichzeitige Sitzungen: Die Datei ist nur-anhängen, .gitattributes setzt
merge=union; nach jedem Rebase wird die Datei geprueft. Echte Konflikte werden
gemeldet, der lokale Commit bleibt erhalten.

Exit-Codes: 0 ok, 2 abgelehnt/ungültig, 3 Tagebuch nicht geladen,
4 lokal gespeichert aber NICHT auf GitHub, 5 Git-Konflikt.
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

try:
    import fcntl
except ImportError:  # pragma: no cover (nicht Linux/macOS)
    fcntl = None

STRATEGIEN = ("kurzfristig", "spekulativ")
STRATEGIE_TITEL = {"kurzfristig": "Kurzfristiges Trading", "spekulativ": "Spekulative Positionen"}
LEDGER = os.path.join("journal", "ausfuehrungen.jsonl")
SETTINGS = os.path.join("journal", "einstellungen.json")
DEFAULT_SETTINGS = {
    "waehrung": "EUR",
    "startkapital_eur": "500.00",
    "gewinne_erhöhen_budget": False,
    "hinweis": "Gesamtbudget für beide Strategien zusammen. Gewinne erhöhen das Budget nur, "
               "wenn gewinne_erhöhen_budget true ist. Verluste verringern es immer.",
}
PUSH_VERSUCHE = 4
CENT = Decimal("0.01")

EXIT_OK, EXIT_ABGELEHNT, EXIT_NICHT_GELADEN, EXIT_NICHT_GESICHERT, EXIT_KONFLIKT = 0, 2, 3, 4, 5

NICHT_GELADEN_HINWEIS = (
    "Es sind KEINE Positionen, Guthaben oder Trades bekannt. Nichts davon annehmen oder schätzen.")


class JournalFehler(Exception):
    code = EXIT_ABGELEHNT


class NichtGeladen(JournalFehler):
    code = EXIT_NICHT_GELADEN


class NichtGesichert(JournalFehler):
    code = EXIT_NICHT_GESICHERT


class Konflikt(JournalFehler):
    code = EXIT_KONFLIKT


# ---------------------------------------------------------------- Zahlen und Zeit

def betrag(text, feld="Betrag", null_erlaubt=True):
    """Deutsche oder englische Schreibweise: 318,18 / 318.18 / 1.234,56."""
    s = str(text).strip().replace("€", "").replace(" ", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise JournalFehler("%s %r ist keine Zahl." % (feld, text))
    if not d.is_finite() or d < 0 or (d == 0 and not null_erlaubt):
        raise JournalFehler("%s muss %s sein (bekommen: %s)." % (
            feld, "größer als 0" if not null_erlaubt else "0 oder größer", text))
    return d


def eur(d, vorzeichen=False):
    d = Decimal(d).quantize(CENT, rounding=ROUND_HALF_UP)
    neg = d < 0
    ganz, _, nach = ("%.2f" % abs(d)).partition(".")
    gruppen = []
    while len(ganz) > 3:
        gruppen.insert(0, ganz[-3:])
        ganz = ganz[:-3]
    gruppen.insert(0, ganz)
    s = "%s,%s €" % (".".join(gruppen), nach)
    if neg:
        return "-" + s
    return ("+" + s) if (vorzeichen and d > 0) else s


def zahl(d):
    d = Decimal(d).normalize()
    s = format(d, "f")
    return s.replace(".", ",")


def _letzter_sonntag(jahr, monat):
    d = datetime.datetime(jahr, monat, 31, 1, 0, tzinfo=datetime.timezone.utc)
    while d.weekday() != 6:
        d -= datetime.timedelta(days=1)
    return d


def deutsche_zeit(utc=None):
    """(datetime ohne tz, 'MEZ'|'MESZ') für Europe/Berlin, ohne zoneinfo (Python 3.8)."""
    utc = utc or datetime.datetime.now(datetime.timezone.utc)
    sommer = _letzter_sonntag(utc.year, 3) <= utc < _letzter_sonntag(utc.year, 10)
    lokal = (utc + datetime.timedelta(hours=2 if sommer else 1)).replace(tzinfo=None)
    return lokal, ("MESZ" if sommer else "MEZ")


def jetzt_utc_text():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def neue_id():
    return "%s-%s" % (datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S"),
                      os.urandom(3).hex())


def zeit_pruefen(text):
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%d.%m.%Y %H:%M"):
        try:
            return datetime.datetime.strptime(text.strip(), fmt).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
    raise JournalFehler("Zeit %r nicht erkannt. Beispiel: \"2026-11-17 16:15\" oder \"17.11.2026 16:15\"." % text)


# ---------------------------------------------------------------- Git

def git(verz, *args, pruefen=True, timeout=120):
    try:
        p = subprocess.run(["git", "-C", verz] + list(args), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise JournalFehler("git %s: Zeitüberschreitung nach %d s." % (" ".join(args[:2]), timeout))
    if pruefen and p.returncode != 0:
        raise JournalFehler("git %s fehlgeschlagen: %s" % (" ".join(args[:2]), (p.stderr or p.stdout).strip()))
    return p


def ist_repo(verz):
    return os.path.isdir(verz) and git(verz, "rev-parse", "--is-inside-work-tree", pruefen=False).returncode == 0


def zweig(verz):
    p = git(verz, "symbolic-ref", "--short", "HEAD", pruefen=False)
    if p.returncode != 0:
        raise JournalFehler("Das Tagebuch steht auf keinem Zweig (detached HEAD). Bitte prüfen.")
    return p.stdout.strip()


def hat_commits(verz):
    return git(verz, "rev-parse", "--verify", "-q", "HEAD", pruefen=False).returncode == 0


def kurz_sha(verz, ref="HEAD"):
    p = git(verz, "rev-parse", "--short", ref, pruefen=False)
    return p.stdout.strip() if p.returncode == 0 else "-"


def remote_sha(verz, branch):
    p = git(verz, "ls-remote", "origin", "refs/heads/" + branch, pruefen=False, timeout=60)
    if p.returncode != 0:
        raise NichtGesichert("GitHub nicht erreichbar: %s" % p.stderr.strip())
    teile = p.stdout.split()
    return teile[0] if teile else None


def git_identitaet(verz):
    if not git(verz, "config", "user.email", pruefen=False).stdout.strip():
        git(verz, "config", "user.email", "trading-journal@localhost")
    if not git(verz, "config", "user.name", pruefen=False).stdout.strip():
        git(verz, "config", "user.name", "Trading-Journal")


def ungesichert(verz):
    """(lokale Änderungen?, Anzahl lokaler Commits, die nicht auf origin sind | None)."""
    dirty = bool(git(verz, "status", "--porcelain", pruefen=False).stdout.strip())
    if not hat_commits(verz):
        return dirty, None
    b = zweig(verz)
    p = git(verz, "rev-list", "--count", "origin/%s..HEAD" % b, pruefen=False)
    if p.returncode != 0:
        return dirty, None
    return dirty, int(p.stdout.strip() or 0)


def speichern(verz, pfade, nachricht):
    """Commit + Push ohne Force. Gibt den bestaetigten Commit zurück oder wirft
    NichtGesichert/Konflikt. Ein lokaler Commit wird nie verworfen."""
    git_identitaet(verz)
    if pfade:
        git(verz, "add", "--", *pfade)
    else:
        git(verz, "add", "-A")
    if git(verz, "diff", "--cached", "--quiet", pruefen=False).returncode != 0:
        git(verz, "commit", "-q", "-m", nachricht)
    if not hat_commits(verz):
        raise JournalFehler("Nichts zu speichern.")
    b = zweig(verz)
    letzter = ""
    for versuch in range(1, PUSH_VERSUCHE + 1):
        f = git(verz, "fetch", "-q", "origin", pruefen=False, timeout=90)
        if f.returncode != 0:
            letzter = f.stderr.strip()
        else:
            hat_remote = git(verz, "rev-parse", "--verify", "-q", "origin/" + b, pruefen=False).returncode == 0
            if hat_remote:
                hinter = git(verz, "rev-list", "--count", "HEAD..origin/" + b).stdout.strip()
                if hinter != "0":
                    r = git(verz, "rebase", "-q", "origin/" + b, pruefen=False)
                    if r.returncode != 0:
                        git(verz, "rebase", "--abort", pruefen=False)
                        raise Konflikt(
                            "Git-Konflikt mit einer anderen Sitzung. Nichts wurde überschrieben: "
                            "dein Eintrag liegt als lokaler Commit %s vor, GitHub ist unverändert. "
                            "Bitte den Konflikt gemeinsam mit Claude von Hand lösen (kein Force-Push)."
                            % kurz_sha(verz))
                    probleme = pruefe_datei(verz)
                    if probleme:
                        raise Konflikt("Nach dem Zusammenführen ist das Tagebuch ungültig, nicht gepusht: "
                                       + "; ".join(probleme[:3]))
            p = git(verz, "push", "-q", "origin", "HEAD:refs/heads/" + b, pruefen=False, timeout=90)
            if p.returncode == 0:
                lokal = git(verz, "rev-parse", "HEAD").stdout.strip()
                if remote_sha(verz, b) == lokal:
                    git(verz, "fetch", "-q", "origin", pruefen=False, timeout=90)
                    return lokal[:7]
                letzter = "GitHub meldet einen anderen Stand als lokal."
            else:
                letzter = p.stderr.strip()
        if versuch < PUSH_VERSUCHE:
            time.sleep(2 ** versuch)
    raise NichtGesichert("Push nach %d Versuchen fehlgeschlagen (%s). Der Eintrag ist nur LOKAL "
                         "gespeichert (Commit %s) und geht verloren, wenn die Sitzung jetzt endet. "
                         "Erneut versuchen: journal.py sichern" % (PUSH_VERSUCHE, letzter, kurz_sha(verz)))


class Sperre(object):
    """Eine Schreibsperre pro Tagebuch, falls zwei Prozesse im selben Container schreiben."""

    def __init__(self, verz):
        self.pfad = os.path.join(verz, "journal", ".lock")
        self.fh = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.pfad), exist_ok=True)
        self.fh = open(self.pfad, "w")
        if fcntl:
            fcntl.flock(self.fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if fcntl:
            fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


# ---------------------------------------------------------------- Tagebuch laden

def tagebuch_verz(arg=None):
    """Explizit (--dir), dann TRADER_JOURNAL_DIR, dann der Symlink ./trader. Sonst NichtGeladen."""
    kandidaten = []
    if arg:
        kandidaten.append(arg)
    elif os.environ.get("TRADER_JOURNAL_DIR"):
        kandidaten.append(os.environ["TRADER_JOURNAL_DIR"])
    elif os.path.islink("trader"):
        kandidaten.append(os.path.dirname(os.path.realpath("trader")))
    if not kandidaten:
        raise NichtGeladen("Kein Tagebuch gefunden (weder --dir noch TRADER_JOURNAL_DIR noch Symlink trader/).")
    verz = os.path.abspath(kandidaten[0])
    if not ist_repo(verz):
        raise NichtGeladen("%s ist kein Git-Repository." % verz)
    if not os.path.isfile(os.path.join(verz, LEDGER)):
        raise NichtGeladen("%s hat keine Datei %s. Neues Tagebuch? Dann: journal.py init --dir %s"
                           % (verz, LEDGER, verz))
    return verz


def lade_einstellungen(verz):
    pfad = os.path.join(verz, SETTINGS)
    s = dict(DEFAULT_SETTINGS)
    if os.path.exists(pfad):
        with open(pfad, encoding="utf-8") as fh:
            s.update(json.load(fh))
    return s


def lese_zeilen(verz):
    """[(zeilennummer, dict | None, rohtext)]"""
    aus = []
    with open(os.path.join(verz, LEDGER), encoding="utf-8") as fh:
        for i, roh in enumerate(fh, 1):
            if not roh.strip():
                continue
            try:
                aus.append((i, json.loads(roh), roh))
            except ValueError:
                aus.append((i, None, roh))
    return aus


def eintraege(verz):
    return [d for _, d, _ in lese_zeilen(verz) if d is not None]


def pruefe_datei(verz):
    probleme = []
    ids = {}
    gueltig = []
    for nr, d, _ in lese_zeilen(verz):
        if d is None or not isinstance(d, dict):
            probleme.append("Zeile %d ist kein gültiges JSON" % nr)
            continue
        i = d.get("id")
        if not i:
            probleme.append("Zeile %d hat keine id" % nr)
            continue
        if i in ids:
            probleme.append("id %s doppelt (Zeilen %d und %d)" % (i, ids[i], nr))
        ids[i] = nr
        typ = d.get("typ")
        if typ in ("kauf", "verkauf"):
            if d.get("strategie") not in STRATEGIEN:
                probleme.append("Zeile %d: unbekannte Strategie %r" % (nr, d.get("strategie")))
            for k in ("symbol", "stueck", "kurs_eur", "gebuehren_eur", "zeit"):
                if k not in d:
                    probleme.append("Zeile %d: Feld %s fehlt" % (nr, k))
        elif typ != "storno":
            probleme.append("Zeile %d: unbekannter Typ %r" % (nr, typ))
        gueltig.append(d)
    for d in gueltig:
        if d.get("typ") == "storno" and d.get("bezug") not in ids:
            probleme.append("Storno %s verweist auf unbekannte id %s" % (d["id"], d.get("bezug")))
    if not probleme:
        try:
            berechne(gueltig, DEFAULT_SETTINGS, mit_fiktiven=True)
        except JournalFehler as e:
            probleme.append(str(e))
    return probleme


# ---------------------------------------------------------------- Berechnung

class Ergebnis(object):
    def __init__(self):
        self.offen = {s: {} for s in STRATEGIEN}       # strategie -> symbol -> [Los]
        self.verkaeufe = {s: [] for s in STRATEGIEN}   # abgeschlossene Verkäufe
        self.gebuehren = {s: Decimal(0) for s in STRATEGIEN}
        self.kaeufe = {s: 0 for s in STRATEGIEN}
        self.startkapital = Decimal(0)
        self.realisiert = Decimal(0)
        self.gebunden = Decimal(0)
        self.budgetbasis = Decimal(0)
        self.frei = Decimal(0)
        self.bargeld = Decimal(0)
        self.anzahl = 0


def berechne(liste, einstellungen, mit_fiktiven=False):
    """FIFO je Strategie und Wertpapier. Kaufgebühren erhöhen den Einstand,
    Verkaufsgebühren mindern den Erlös."""
    storniert = {d.get("bezug") for d in liste if d.get("typ") == "storno"}
    aktiv = [d for d in liste if d.get("typ") in ("kauf", "verkauf") and d.get("id") not in storniert
             and (mit_fiktiven or not d.get("fiktiv"))]
    aktiv.sort(key=lambda d: (d.get("zeit", ""), d.get("erfasst_utc", ""), d.get("id", "")))
    e = Ergebnis()
    e.anzahl = len(aktiv)
    for d in aktiv:
        s, sym = d["strategie"], d["symbol"].upper()
        stk, kurs, geb = Decimal(d["stueck"]), Decimal(d["kurs_eur"]), Decimal(d["gebuehren_eur"])
        e.gebuehren[s] += geb
        lose = e.offen[s].setdefault(sym, [])
        if d["typ"] == "kauf":
            e.kaeufe[s] += 1
            lose.append({"id": d["id"], "stueck": stk, "kurs": kurs, "stueck_kosten": (stk * kurs + geb) / stk,
                         "stop": Decimal(d["stop_eur"]) if d.get("stop_eur") else None,
                         "zeit": d["zeit"], "name": d.get("name") or "", "isin": d.get("isin") or "",
                         "kaufgeb": geb, "kaufgeb_stueck": geb / stk})
            continue
        bestand = sum((l["stueck"] for l in lose), Decimal(0))
        if stk > bestand:
            raise JournalFehler("Verkauf %s: %s Stück %s (%s), aber im Tagebuch sind nur %s Stück."
                                % (d["id"], zahl(stk), sym, s, zahl(bestand)))
        rest, kosten, risiko, kaufgeb = stk, Decimal(0), Decimal(0), Decimal(0)
        risiko_bekannt = True
        while rest > 0:
            l = lose[0]
            nimm = min(rest, l["stueck"])
            kosten += nimm * l["stueck_kosten"]
            kaufgeb += nimm * l["kaufgeb_stueck"]
            if l["stop"] is not None and l["kurs"] > l["stop"]:
                risiko += nimm * (l["kurs"] - l["stop"])
            else:
                risiko_bekannt = False
            l["stueck"] -= nimm
            rest -= nimm
            if l["stueck"] == 0:
                lose.pop(0)
        if not lose:
            del e.offen[s][sym]
        erloes = stk * kurs - geb
        ergebnis = erloes - kosten
        e.verkaeufe[s].append({
            "id": d["id"], "zeit": d["zeit"], "symbol": sym, "stueck": stk, "erloes": erloes,
            "kosten": kosten, "ergebnis": ergebnis, "brutto": ergebnis + geb + kaufgeb,
            "gebuehren": geb + kaufgeb, "r": (ergebnis / risiko) if (risiko_bekannt and risiko > 0) else None})
        e.realisiert += ergebnis
    for s in STRATEGIEN:
        for lose in e.offen[s].values():
            for l in lose:
                e.gebunden += l["stueck"] * l["stueck_kosten"]
    e.startkapital = Decimal(str(einstellungen.get("startkapital_eur", "500")))
    gewinne = bool(einstellungen.get("gewinne_erhöhen_budget", False))
    e.budgetbasis = e.startkapital + (e.realisiert if gewinne else min(e.realisiert, Decimal(0)))
    e.frei = e.budgetbasis - e.gebunden
    e.bargeld = e.startkapital + e.realisiert - e.gebunden
    return e


# ---------------------------------------------------------------- Ausgabe

def warnung_ungesichert(verz):
    dirty, voraus = ungesichert(verz)
    if voraus:
        return "ACHTUNG: %d Commit(s) nur lokal, noch NICHT auf GitHub. journal.py sichern ausführen." % voraus
    if dirty:
        return "Hinweis: Es gibt ungesicherte Änderungen (z. B. Arbeitsstände der Skills)."
    return "Alles ist auf GitHub gesichert."


def kapital_zeilen(e):
    z = ["Kapital (Gesamtbudget für beide Strategien, in Euro):",
         "  Startkapital            %s" % eur(e.startkapital),
         "  Realisiert (netto)      %s" % eur(e.realisiert, True),
         "  Gebunden in Positionen  %s  (Einstand inkl. Kaufgebühren)" % eur(e.gebunden),
         "  Frei für neue Käufe     %s" % eur(e.frei)]
    if e.bargeld != e.frei:
        z.append("  (Spielgeld rechnerisch %s; Gewinne erhöhen das Budget laut Einstellung nicht.)"
                 % eur(e.bargeld))
    if e.frei < 0:
        z.append("  WARNUNG: Das Budget ist überschritten. Keine neuen Käufe.")
    return z


def status_text(verz, kurz=False):
    s = lade_einstellungen(verz)
    e = berechne(eintraege(verz), s)
    b = zweig(verz) if hat_commits(verz) else "-"
    z = ["Tagebuch geladen: %s (Zweig %s, Commit %s)." % (verz, b, kurz_sha(verz)),
         warnung_ungesichert(verz), ""]
    z += kapital_zeilen(e)
    for st in STRATEGIEN:
        pos = e.offen[st]
        if pos:
            z.append("%s: %d offene Position(en): %s" % (STRATEGIE_TITEL[st], len(pos), ", ".join(
                "%s %s Stk" % (sym, zahl(sum((l["stueck"] for l in lose), Decimal(0))))
                for sym, lose in sorted(pos.items()))))
        else:
            z.append("%s: keine offenen Positionen." % STRATEGIE_TITEL[st])
    if e.anzahl == 0:
        z.append("Noch keine echten Ausführungen erfasst.")
    return "\n".join(z)


def bericht_text(verz, strategien, kurse, mit_fiktiven=False):
    s = lade_einstellungen(verz)
    e = berechne(eintraege(verz), s, mit_fiktiven=mit_fiktiven)
    lokal, tz = deutsche_zeit()
    z = ["# Trading-Tagebuch: Auswertung", "",
         "Stand: %s %s, Tagebuch-Commit %s.%s" % (lokal.strftime("%d.%m.%Y %H:%M"), tz, kurz_sha(verz),
                                                 " Enthält FIKTIVE Testdaten." if mit_fiktiven else ""),
         warnung_ungesichert(verz), ""]
    z += kapital_zeilen(e)
    for st in strategien:
        z += ["", "## %s" % STRATEGIE_TITEL[st], ""]
        pos = e.offen[st]
        if pos:
            z.append("Offene Positionen:")
            for sym, lose in sorted(pos.items()):
                stk = sum((l["stueck"] for l in lose), Decimal(0))
                einstand = sum((l["stueck"] * l["stueck_kosten"] for l in lose), Decimal(0))
                name = lose[0]["name"] or sym
                isin = (" (ISIN %s)" % lose[0]["isin"]) if lose[0]["isin"] else ""
                zeile = "  %s%s: %s Stück, Einstand %s (%s je Stück inkl. Gebühren)" % (
                    name, isin, zahl(stk), eur(einstand), eur(einstand / stk))
                stops = [l["stop"] for l in lose if l["stop"] is not None]
                if stops:
                    zeile += ", Stop %s" % eur(min(stops))
                z.append(zeile)
                if sym in kurse:
                    wert = stk * kurse[sym]
                    z.append("      aktueller Kurs %s (von dir angegeben): Wert %s, nicht realisiert %s"
                             % (eur(kurse[sym]), eur(wert), eur(wert - einstand, True)))
        else:
            z.append("Keine offenen Positionen.")
        v = e.verkaeufe[st]
        z.append("")
        if v:
            gew = [x for x in v if x["ergebnis"] > 0]
            summe = sum((x["ergebnis"] for x in v), Decimal(0))
            brutto = sum((x["brutto"] for x in v), Decimal(0))
            geb = sum((x["gebuehren"] for x in v), Decimal(0))
            z += ["Abgeschlossene Verkäufe: %d (Gewinner %d, Verlierer %d, Trefferquote %.0f %%)"
                  % (len(v), len(gew), len(v) - len(gew), 100.0 * len(gew) / len(v)),
                  "  Ergebnis vor Gebühren  %s" % eur(brutto, True),
                  "  Gebühren dieser Trades %s" % eur(geb),
                  "  Ergebnis nach Gebühren %s" % eur(summe, True)]
            if brutto > 0:
                z.append("  Die Gebühren haben %.0f %% des Brutto-Gewinns aufgezehrt." % (100 * geb / brutto))
            rs = [x["r"] for x in v if x["r"] is not None]
            if st == "kurzfristig" and rs:
                z.append("  Durchschnittliches R: %s (R = Ergebnis geteilt durch das geplante Risiko bis zum Stop; "
                         "%d von %d Trades mit Stop)"
                         % (zahl((sum(rs, Decimal(0)) / len(rs)).quantize(CENT)), len(rs), len(v)))
            z.append("  Einzelne Verkäufe:")
            for x in v:
                z.append("    %s  %s %s Stk: %s%s" % (x["zeit"], x["symbol"], zahl(x["stueck"]),
                                                    eur(x["ergebnis"], True),
                                                    (" (%s R)" % zahl(x["r"].quantize(CENT))) if x["r"] is not None else ""))
        else:
            z.append("Noch keine abgeschlossenen Verkäufe.")
        z.append("Alle Gebühren dieser Strategie (Kauf und Verkauf): %s" % eur(e.gebuehren[st]))
        if st == "spekulativ":
            z.append("Hinweis: Spekulative Aktien können bis zum Totalverlust der Position fallen.")
    z += ["", "Rechnung nach FIFO (zuerst gekaufte Stücke gelten als zuerst verkauft). Kaufgebühren "
          "erhöhen den Einstand, Verkaufsgebühren mindern den Erlös. Steuern sind nicht berücksichtigt.",
          "Analyse und Rechnung, keine Anlageberatung."]
    return "\n".join(z)


# ---------------------------------------------------------------- Befehle

def aktualisieren(verz):
    """Holt vor einer Eintragung den neuesten Stand von GitHub (fetch + rebase),
    damit Budget- und Bestandspruefung auch Eintraege anderer Sitzungen sehen.
    Gibt eine Warnung zurueck oder None."""
    if not hat_commits(verz) or not git(verz, "remote", pruefen=False).stdout.strip():
        return None
    b = zweig(verz)
    if git(verz, "fetch", "-q", "origin", pruefen=False, timeout=90).returncode != 0:
        return "Warnung: GitHub nicht erreichbar, geprüft wird gegen den lokalen Stand."
    if git(verz, "rev-parse", "--verify", "-q", "origin/" + b, pruefen=False).returncode != 0:
        return None
    if git(verz, "rev-list", "--count", "HEAD..origin/" + b).stdout.strip() == "0":
        return None
    if git(verz, "status", "--porcelain", "--untracked-files=no", pruefen=False).stdout.strip():
        return "Warnung: Lokale Änderungen, neuester GitHub-Stand nicht eingespielt."
    if git(verz, "rebase", "-q", "origin/" + b, pruefen=False).returncode != 0:
        git(verz, "rebase", "--abort", pruefen=False)
        raise Konflikt("Git-Konflikt beim Aktualisieren. Nichts erfasst, nichts überschrieben.")
    return None


def schreibe_und_speichere(verz, eintrag, nachricht):
    """Haengt eine Zeile an (mit fsync) und speichert sofort auf GitHub."""
    pfad = os.path.join(verz, LEDGER)
    with open(pfad, "rb") as fh:
        inhalt = fh.read()
    with open(pfad, "a", encoding="utf-8") as fh:
        if inhalt and not inhalt.endswith(b"\n"):
            fh.write("\n")
        fh.write(json.dumps(eintrag, ensure_ascii=False, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    sha = speichern(verz, [LEDGER], nachricht)
    return sha


def cmd_ausfuehrung(a, typ):
    verz = tagebuch_verz(a.dir)
    if a.strategie not in STRATEGIEN:
        raise JournalFehler("Strategie muss kurzfristig oder spekulativ sein.")
    sym = a.symbol.strip().upper()
    stk = betrag(a.stueck, "Stückzahl", null_erlaubt=False)
    kurs = betrag(a.kurs, "Kurs", null_erlaubt=False)
    geb = betrag(a.gebuehren, "Gebühren")
    if a.zeit:
        zeit, tz = zeit_pruefen(a.zeit), a.zeitzone
    else:
        lokal, tz = deutsche_zeit()
        zeit = lokal.strftime("%Y-%m-%d %H:%M")
    eintrag = {"v": 1, "id": neue_id(), "typ": typ, "strategie": a.strategie, "symbol": sym,
               "stueck": str(stk), "kurs_eur": str(kurs), "gebuehren_eur": str(geb), "zeit": zeit,
               "zeitzone": tz, "erfasst_utc": jetzt_utc_text(), "fiktiv": bool(a.fiktiv)}
    for k, feld in (("name", "name"), ("isin", "isin"), ("handelsplatz", "handelsplatz"), ("notiz", "notiz")):
        wert = getattr(a, feld, None)
        if wert:
            eintrag[k] = wert.strip()
    if typ == "kauf":
        if a.stop:
            stop = betrag(a.stop, "Stop", null_erlaubt=False)
            if stop >= kurs:
                raise JournalFehler("Der Stop (%s) muss unter dem Kaufkurs (%s) liegen." % (eur(stop), eur(kurs)))
            eintrag["stop_eur"] = str(stop)
        if a.ziel:
            eintrag["ziel_eur"] = str(betrag(a.ziel, "Ziel", null_erlaubt=False))
        if a.strategie == "spekulativ":
            eintrag["totalverlust_moeglich"] = True
    with Sperre(verz):
        warnung = aktualisieren(verz)
        if warnung:
            print(warnung)
        einst = lade_einstellungen(verz)
        alle = eintraege(verz)
        vorher = berechne(alle, einst, mit_fiktiven=bool(a.fiktiv))
        if typ == "kauf":
            kosten = stk * kurs + geb
            if kosten > vorher.frei:
                if not a.ueber_budget_bestaetigt:
                    raise JournalFehler(
                        "Abgelehnt: Kauf kostet %s, frei sind nur %s von %s Budget. Nichts gespeichert. "
                        "Falls du den Kauf trotzdem bereits ausgeführt hast, mit --ueber-budget-bestaetigt "
                        "erfassen (das Tagebuch muss die Wirklichkeit zeigen)."
                        % (eur(kosten), eur(vorher.frei), eur(vorher.budgetbasis)))
                eintrag["ueber_budget"] = True
        try:
            berechne(alle + [eintrag], einst, mit_fiktiven=bool(a.fiktiv))
        except JournalFehler as e:
            raise JournalFehler("Abgelehnt, nichts gespeichert: %s" % e)
        art = "Kauf" if typ == "kauf" else "Verkauf"
        nachricht = "%s%s: %s %s zu %s (%s)" % ("[FIKTIV] " if a.fiktiv else "", art, zahl(stk), sym,
                                                eur(kurs), a.strategie)
        sha = schreibe_und_speichere(verz, eintrag, nachricht)
        # nach dem Push aus der Datei rechnen: enthaelt auch Eintraege anderer Sitzungen
        nachher = berechne(eintraege(verz), einst, mit_fiktiven=bool(a.fiktiv))
    print("%s erfasst%s: %s %s Stück %s zu %s, Gebühren %s, Strategie %s, Zeit %s %s."
          % (art, " (FIKTIV)" if a.fiktiv else "", art, zahl(stk), sym, eur(kurs), eur(geb),
             a.strategie, zeit, tz))
    if typ == "verkauf":
        v = [x for x in nachher.verkaeufe[a.strategie] if x["id"] == eintrag["id"]][0]
        print("Ergebnis dieses Verkaufs nach Gebühren: %s." % eur(v["ergebnis"], True))
    print("Frei für neue Käufe: %s." % eur(nachher.frei))
    if eintrag.get("ueber_budget"):
        print("WARNUNG: Dieser Kauf überschreitet das Budget.")
    print("Gespeichert ✓ auf GitHub (Commit %s, id %s)." % (sha, eintrag["id"]))
    return EXIT_OK


def cmd_storno(a):
    verz = tagebuch_verz(a.dir)
    with Sperre(verz):
        warnung = aktualisieren(verz)
        if warnung:
            print(warnung)
        alle = eintraege(verz)
        ziel = [d for d in alle if d.get("id") == a.id and d.get("typ") in ("kauf", "verkauf")]
        if not ziel:
            raise JournalFehler("Keine Ausführung mit id %s gefunden." % a.id)
        if any(d.get("typ") == "storno" and d.get("bezug") == a.id for d in alle):
            raise JournalFehler("%s ist bereits storniert." % a.id)
        eintrag = {"v": 1, "id": neue_id(), "typ": "storno", "bezug": a.id, "grund": a.grund,
                   "erfasst_utc": jetzt_utc_text(), "fiktiv": bool(ziel[0].get("fiktiv"))}
        try:
            berechne(alle + [eintrag], lade_einstellungen(verz), mit_fiktiven=True)
        except JournalFehler as e:
            raise JournalFehler("Storno abgelehnt (danach wäre der Bestand negativ): %s" % e)
        sha = schreibe_und_speichere(verz, eintrag, "Storno %s: %s" % (a.id, a.grund))
    print("Storniert: %s (%s). Die Originalzeile bleibt zur Nachvollziehbarkeit erhalten." % (a.id, a.grund))
    print("Gespeichert ✓ auf GitHub (Commit %s)." % sha)
    return EXIT_OK


def cmd_status(a):
    verz = tagebuch_verz(a.dir)
    probleme = pruefe_datei(verz)
    print(status_text(verz))
    if probleme:
        print("PROBLEME im Tagebuch: " + "; ".join(probleme))
        return EXIT_ABGELEHNT
    return EXIT_OK


def cmd_bericht(a):
    verz = tagebuch_verz(a.dir)
    kurse = {}
    for k in a.kurs or []:
        sym, _, wert = k.partition("=")
        kurse[sym.strip().upper()] = betrag(wert, "Kurs %s" % sym, null_erlaubt=False)
    strategien = [a.strategie] if a.strategie else list(STRATEGIEN)
    text = bericht_text(verz, strategien, kurse, mit_fiktiven=a.mit_fiktiven)
    print(text)
    if a.datei:
        pfad = os.path.join(verz, a.datei)
        os.makedirs(os.path.dirname(pfad) or verz, exist_ok=True)
        with open(pfad, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        sha = speichern(verz, [a.datei], "Bericht %s" % a.datei)
        print("\nBericht gespeichert ✓ auf GitHub: %s (Commit %s)." % (a.datei, sha))
    return EXIT_OK


def cmd_liste(a):
    verz = tagebuch_verz(a.dir)
    alle = eintraege(verz)
    storniert = {d.get("bezug") for d in alle if d.get("typ") == "storno"}
    for d in alle[-a.anzahl:]:
        if d.get("typ") == "storno":
            print("%s  STORNO von %s: %s" % (d["id"], d.get("bezug"), d.get("grund", "")))
            continue
        print("%s  %s %s  %-11s %-5s %s Stk zu %s, Gebühren %s%s%s" % (
            d["id"], d["zeit"], d.get("zeitzone", ""), d["strategie"], d["symbol"], zahl(Decimal(d["stueck"])),
            eur(Decimal(d["kurs_eur"])), eur(Decimal(d["gebuehren_eur"])),
            " [FIKTIV]" if d.get("fiktiv") else "", " [STORNIERT]" if d["id"] in storniert else ""))
    if not alle:
        print("Noch keine Einträge.")
    return EXIT_OK


def cmd_pruefen(a):
    verz = tagebuch_verz(a.dir)
    probleme = pruefe_datei(verz)
    if probleme:
        print("Tagebuch UNGÜLTIG:\n  " + "\n  ".join(probleme))
        return EXIT_ABGELEHNT
    print("Tagebuch gültig: %d Zeilen, keine doppelten ids, kein negativer Bestand." % len(lese_zeilen(verz)))
    print(warnung_ungesichert(verz))
    return EXIT_OK


def cmd_sichern(a):
    try:
        verz = tagebuch_verz(a.dir)
    except NichtGeladen:
        if a.hook:
            return EXIT_OK
        raise
    dirty, voraus = ungesichert(verz)
    if not dirty and voraus == 0:
        if not a.hook:
            print("Alles ist bereits auf GitHub gesichert (Commit %s)." % kurz_sha(verz))
        return EXIT_OK
    try:
        with Sperre(verz):
            sha = speichern(verz, None, "Arbeitsstände sichern")
    except JournalFehler as e:
        if a.hook:
            print(json.dumps({"systemMessage": "Trading-Tagebuch NICHT gesichert: %s" % e}, ensure_ascii=False))
            return EXIT_OK
        raise
    if not a.hook:
        print("Gesichert ✓ auf GitHub (Commit %s)." % sha)
    return EXIT_OK


def cmd_init(a):
    verz = os.path.abspath(a.dir or os.environ.get("TRADER_JOURNAL_DIR") or ".")
    if not ist_repo(verz):
        raise JournalFehler("%s ist kein Git-Repository (erst klonen)." % verz)
    if os.path.exists(os.path.join(verz, LEDGER)):
        print("Tagebuch existiert bereits in %s. Nichts geändert." % verz)
        return EXIT_OK
    if not hat_commits(verz):
        git(verz, "symbolic-ref", "HEAD", "refs/heads/main")
    dateien = {
        LEDGER: "",
        SETTINGS: json.dumps(DEFAULT_SETTINGS, indent=2, ensure_ascii=False) + "\n",
        ".gitattributes": "# Gleichzeitige Sitzungen: angehängte Zeilen beider Seiten behalten\n"
                          "journal/*.jsonl merge=union\n",
        ".gitignore": "journal/.lock\n__pycache__/\n*.pyc\ntrader/state/ptr-pdfs/\n",
        os.path.join("trader", ".gitkeep"): "",
        os.path.join("berichte", ".gitkeep"): "",
        "README.md": README_PRIVAT,
    }
    for rel, inhalt in dateien.items():
        pfad = os.path.join(verz, rel)
        if os.path.exists(pfad):
            continue
        os.makedirs(os.path.dirname(pfad) or verz, exist_ok=True)
        with open(pfad, "w", encoding="utf-8") as fh:
            fh.write(inhalt)
    sha = speichern(verz, None, "Tagebuch einrichten")
    print("Tagebuch eingerichtet in %s, gespeichert ✓ auf GitHub (Commit %s)." % (verz, sha))
    return EXIT_OK


README_PRIVAT = """# Trading-Tagebuch (privat)

Dieses Repository ist PRIVAT und enthält persönliche Handelsdaten.
Nicht öffentlich machen, nicht forken, keine Inhalte ins öffentliche
Repository day-trader-skill kopieren.

- `journal/ausfuehrungen.jsonl`: jede vom Nutzer gemeldete Ausführung als eine
  Zeile (nur anhängen; Korrekturen als Storno-Zeile).
- `journal/einstellungen.json`: Startkapital in Euro (Gesamtbudget beider Strategien).
- `trader/`: Arbeitsdateien der Skills (Konfiguration, Risiko-Zaehler, Alarme).
- `berichte/`: erzeugte deutsche Auswertungen.

Geschrieben wird nur mit `trader-journal/journal.py` aus dem Repository
day-trader-skill. Kein Force-Push. Keine Brokeranbindung, keine Orders.
"""


def cmd_start(a):
    """SessionStart: Tagebuch klonen oder aktualisieren, trader/ verlinken, Lage ausgeben.
    Endet immer mit 0, damit die Sitzung startet; Fehler stehen deutlich im Text."""
    projekt = os.path.abspath(a.projekt or os.environ.get("CLAUDE_PROJECT_DIR") or ".")
    repo = (a.repo or os.environ.get("TRADER_JOURNAL_REPO") or "").strip()
    verz = os.path.abspath(a.dir or os.environ.get("TRADER_JOURNAL_DIR")
                           or os.path.join(os.path.dirname(projekt), "trading-journal"))
    kopf = "=== Trading-Tagebuch (privat) ==="

    def fehlschlag(grund, abhilfe):
        print("\n".join([kopf, "TAGEBUCH NICHT GELADEN: %s" % grund, NICHT_GELADEN_HINWEIS,
                         "Keine Trades erfassen, bis das Tagebuch geladen ist.", "Abhilfe: %s" % abhilfe]))
        return EXIT_OK

    add_repo = ("Claude ruft add_repo für %s (access push) auf und danach erneut "
                "`python3 trader-journal/journal.py start`." % (repo or "das private Tagebuch-Repository"))
    try:
        if ist_repo(verz):
            if hat_commits(verz) and git(verz, "remote", pruefen=False).stdout.strip():
                dirty, voraus = ungesichert(verz)
                if dirty or voraus:
                    try:
                        with Sperre(verz):
                            speichern(verz, None, "Arbeitsstände sichern (Sitzungsstart)")
                    except JournalFehler as e:
                        print("Warnung: Ungesichertes konnte nicht gepusht werden: %s" % e)
                p = git(verz, "pull", "-q", "--rebase", "origin", zweig(verz), pruefen=False, timeout=90)
                if p.returncode != 0:
                    git(verz, "rebase", "--abort", pruefen=False)
                    print("Warnung: Aktualisieren von GitHub fehlgeschlagen (%s). Lokaler Stand wird angezeigt."
                          % p.stderr.strip())
        else:
            if not repo:
                return fehlschlag("TRADER_JOURNAL_REPO ist in der Umgebung nicht gesetzt.",
                                  "In der Cloud-Umgebung die Variable TRADER_JOURNAL_REPO=besitzer/repo setzen.")
            if os.path.exists(verz) and os.listdir(verz):
                return fehlschlag("%s existiert, ist aber kein Git-Repository." % verz,
                                  "Ordner prüfen, nicht automatisch löschen.")
            url = repo if ("://" in repo or repo.startswith("/")) else "https://github.com/%s.git" % repo
            p = subprocess.run(["git", "clone", "-q", url, verz], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               universal_newlines=True, timeout=180)
            if p.returncode != 0:
                return fehlschlag("Klonen von %s fehlgeschlagen: %s" % (repo, p.stderr.strip()), add_repo)
        verz = tagebuch_verz(verz)
        git_identitaet(verz)
    except NichtGeladen as e:
        return fehlschlag(str(e), add_repo)
    except (JournalFehler, OSError, subprocess.TimeoutExpired) as e:
        return fehlschlag(str(e), add_repo)
    link = os.path.join(projekt, "trader")
    ziel = os.path.join(verz, "trader")
    os.makedirs(ziel, exist_ok=True)
    link_hinweis = ""
    if os.path.islink(link):
        if os.path.realpath(link) != os.path.realpath(ziel):
            os.remove(link)
            os.symlink(ziel, link)
    elif os.path.exists(link):
        link_hinweis = ("Warnung: %s ist ein echter Ordner und wurde NICHT ersetzt. Die Skills schreiben "
                        "dorthin statt ins Tagebuch. Inhalt prüfen und ins Tagebuch übernehmen." % link)
    else:
        os.symlink(ziel, link)
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if env_file:
        with open(env_file, "a") as fh:
            fh.write("export TRADER_JOURNAL_DIR=%s\n" % verz)
    os.environ["TRADER_JOURNAL_DIR"] = verz
    probleme = pruefe_datei(verz)
    print(kopf)
    print(status_text(verz))
    if link_hinweis:
        print(link_hinweis)
    if probleme:
        print("PROBLEME im Tagebuch (erst klären, dann handeln): " + "; ".join(probleme))
    print("Regeln: Trades nur erfassen, wenn der Nutzer eine ausgeführte Order ausdrücklich meldet "
          "(journal.py kauf/verkauf). Erst nach 'Gespeichert ✓' als gespeichert bestätigen. "
          "Keine Orders, keine Brokeranbindung.")
    return EXIT_OK


# ---------------------------------------------------------------- CLI

def parser():
    p = argparse.ArgumentParser(description="Trading-Tagebuch in Euro, gespeichert im privaten Git-Repository.")
    sub = p.add_subparsers(dest="cmd")

    def mit_dir(sp):
        sp.add_argument("--dir", help="Ordner des privaten Tagebuch-Repositorys")
        return sp

    for typ in ("kauf", "verkauf"):
        sp = mit_dir(sub.add_parser(typ, help="ausgeführten %s erfassen und sofort speichern" % typ))
        sp.add_argument("--strategie", required=True, choices=STRATEGIEN)
        sp.add_argument("--symbol", required=True)
        sp.add_argument("--stueck", required=True)
        sp.add_argument("--kurs", required=True, help="Ausführungskurs je Stück in Euro")
        sp.add_argument("--gebuehren", required=True, help="Ordergebühren in Euro (0 erlaubt)")
        sp.add_argument("--zeit", help='Ausführungszeit, z. B. "2026-11-17 16:15" (Standard: jetzt)')
        sp.add_argument("--zeitzone", default="MEZ", help="Zeitzone der --zeit (Standard MEZ)")
        sp.add_argument("--name")
        sp.add_argument("--isin")
        sp.add_argument("--handelsplatz")
        sp.add_argument("--notiz")
        sp.add_argument("--fiktiv", action="store_true", help="Testeintrag, nicht in Auswertungen")
        if typ == "kauf":
            sp.add_argument("--stop", help="geplanter Stop in Euro")
            sp.add_argument("--ziel", help="geplantes Ziel in Euro")
            sp.add_argument("--ueber-budget-bestaetigt", action="store_true")
    sp = mit_dir(sub.add_parser("storno", help="Eintrag als fehlerhaft markieren"))
    sp.add_argument("--id", required=True)
    sp.add_argument("--grund", required=True)
    mit_dir(sub.add_parser("status", help="geladen? Kapital und offene Positionen"))
    sp = mit_dir(sub.add_parser("bericht", help="deutsche Auswertung je Strategie"))
    sp.add_argument("--strategie", choices=STRATEGIEN)
    sp.add_argument("--kurs", action="append", help="aktueller Kurs in Euro, z. B. AAPL=320,10")
    sp.add_argument("--datei", help="Bericht zusätzlich im Tagebuch speichern, z. B. berichte/2026-11.md")
    sp.add_argument("--mit-fiktiven", action="store_true")
    sp = mit_dir(sub.add_parser("liste", help="letzte Einträge"))
    sp.add_argument("--anzahl", type=int, default=20)
    mit_dir(sub.add_parser("pruefen", help="Tagebuch auf Fehler prüfen"))
    sp = mit_dir(sub.add_parser("sichern", help="Ungesichertes committen und pushen"))
    sp.add_argument("--hook", action="store_true", help="still, für den Stop-Hook")
    mit_dir(sub.add_parser("init", help="leeres privates Repository einrichten"))
    sp = mit_dir(sub.add_parser("start", help="SessionStart: laden, verlinken, Lage ausgeben"))
    sp.add_argument("--projekt", help="Projektordner (Standard: CLAUDE_PROJECT_DIR)")
    sp.add_argument("--repo", help="besitzer/repo (Standard: TRADER_JOURNAL_REPO)")
    return p


BEFEHLE = {"kauf": lambda a: cmd_ausfuehrung(a, "kauf"), "verkauf": lambda a: cmd_ausfuehrung(a, "verkauf"),
           "storno": cmd_storno, "status": cmd_status, "bericht": cmd_bericht, "liste": cmd_liste,
           "pruefen": cmd_pruefen, "sichern": cmd_sichern, "init": cmd_init, "start": cmd_start}


def main(argv=None):
    a = parser().parse_args(argv)
    if not a.cmd:
        parser().print_help()
        return EXIT_ABGELEHNT
    try:
        return BEFEHLE[a.cmd](a)
    except NichtGeladen as e:
        print("TAGEBUCH NICHT GELADEN: %s\n%s" % (e, NICHT_GELADEN_HINWEIS))
        return e.code
    except JournalFehler as e:
        print(str(e))
        return e.code


if __name__ == "__main__":
    sys.exit(main())
