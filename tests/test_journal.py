"""Tests für trader-journal. Nur fiktive Daten; ein lokales Bare-Repository
steht für GitHub. Kein Netzwerk."""

import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from decimal import Decimal

from _load import load, ROOT

journal = load("trader-journal", "journal")


def sh(cwd, *args):
    return subprocess.run(["git", "-C", cwd] + list(args), stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, universal_newlines=True, check=True).stdout.strip()


def lies(pfad):
    with open(pfad, encoding="utf-8") as fh:
        return fh.read()


def run(argv):
    out = io.StringIO()
    with redirect_stdout(out):
        code = journal.main(argv)
    return code, out.getvalue()


class Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.remote = os.path.join(self.tmp, "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.remote], check=True)
        self.a = self.klon("a")
        journal.time = _OhneSchlaf()
        code, out = run(["init", "--dir", self.a])
        self.assertEqual(code, 0, out)
        self._env = dict(os.environ)
        os.environ.pop("TRADER_JOURNAL_DIR", None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def klon(self, name):
        ziel = os.path.join(self.tmp, name)
        subprocess.run(["git", "clone", "-q", self.remote, ziel], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        sh(ziel, "config", "user.email", "test@example.invalid")
        sh(ziel, "config", "user.name", "Test")
        return ziel

    def kauf(self, verz, *extra, **kw):
        argv = ["kauf", "--dir", verz, "--strategie", kw.get("strategie", "kurzfristig"),
                "--symbol", kw.get("symbol", "TEST"), "--stueck", kw.get("stueck", "1"),
                "--kurs", kw.get("kurs", "100"), "--gebuehren", kw.get("gebuehren", "1"),
                "--zeit", kw.get("zeit", "2026-11-17 16:00"), "--fiktiv"] + list(extra)
        return run(argv)

    def verkauf(self, verz, **kw):
        return run(["verkauf", "--dir", verz, "--strategie", kw.get("strategie", "kurzfristig"),
                    "--symbol", kw.get("symbol", "TEST"), "--stueck", kw.get("stueck", "1"),
                    "--kurs", kw.get("kurs", "110"), "--gebuehren", kw.get("gebuehren", "1"),
                    "--zeit", kw.get("zeit", "2026-11-17 17:00"), "--fiktiv"])

    def remote_ledger(self):
        return subprocess.run(["git", "--git-dir", self.remote, "show", "main:journal/ausfuehrungen.jsonl"],
                              stdout=subprocess.PIPE, universal_newlines=True, check=True).stdout

    def ergebnis(self, verz):
        return journal.berechne(journal.eintraege(verz), journal.lade_einstellungen(verz), mit_fiktiven=True)


class _OhneSchlaf(object):
    """Ersetzt das time-Modul im Journal, damit Wiederholungen nicht warten."""

    def sleep(self, _):
        pass


class Einrichten(Basis):
    def test_init_legt_struktur_an_und_pusht(self):
        for rel in ("journal/ausfuehrungen.jsonl", "journal/einstellungen.json", ".gitattributes",
                    ".gitignore", "README.md", "trader/.gitkeep"):
            self.assertTrue(os.path.exists(os.path.join(self.a, rel)), rel)
        with open(os.path.join(self.a, ".gitattributes")) as fh:
            self.assertIn("merge=union", fh.read())
        self.assertEqual(sh(self.a, "rev-parse", "HEAD"), sh(self.remote, "rev-parse", "main"))
        einst = journal.lade_einstellungen(self.a)
        self.assertEqual(einst["waehrung"], "EUR")
        self.assertEqual(Decimal(einst["startkapital_eur"]), Decimal(500))

    def test_init_ist_idempotent(self):
        code, out = run(["init", "--dir", self.a])
        self.assertEqual(code, 0)
        self.assertIn("existiert bereits", out)


class Speichern(Basis):
    def test_kauf_wird_erst_nach_push_bestaetigt(self):
        code, out = self.kauf(self.a)
        self.assertEqual(code, 0, out)
        self.assertIn("Gespeichert ✓ auf GitHub", out)
        self.assertIn('"symbol": "TEST"', self.remote_ledger())
        self.assertEqual(sh(self.a, "rev-parse", "HEAD"), sh(self.remote, "rev-parse", "main"))

    def test_push_fehler_wird_nicht_als_gespeichert_gemeldet(self):
        weg = self.remote + ".weg"
        os.rename(self.remote, weg)
        code, out = self.kauf(self.a)
        self.assertEqual(code, journal.EXIT_NICHT_GESICHERT, out)
        self.assertNotIn("Gespeichert", out)
        self.assertIn("nur LOKAL", out)
        self.assertIn('"symbol": "TEST"', lies(os.path.join(self.a, journal.LEDGER)))
        os.rename(weg, self.remote)
        code, out = run(["sichern", "--dir", self.a])
        self.assertEqual(code, 0, out)
        self.assertIn('"symbol": "TEST"', self.remote_ledger())

    def test_gleichzeitige_sitzungen_verlieren_nichts(self):
        b = self.klon("b")
        self.assertEqual(self.kauf(self.a, symbol="AAA")[0], 0)
        code, out = self.kauf(b, symbol="BBB")  # b ist veraltet: Rebase mit merge=union
        self.assertEqual(code, 0, out)
        led = self.remote_ledger()
        self.assertIn('"symbol": "AAA"', led)
        self.assertIn('"symbol": "BBB"', led)
        sh(self.a, "pull", "-q", "--rebase")
        self.assertEqual(journal.pruefe_datei(self.a), [])

    def test_budget_sieht_kaeufe_anderer_sitzungen(self):
        b = self.klon("b")
        self.assertEqual(self.kauf(self.a, symbol="AAA", stueck="3", kurs="100")[0], 0)  # 301 gebunden
        code, out = self.kauf(b, symbol="BBB", stueck="3", kurs="100")  # b kennt AAA noch nicht
        self.assertEqual(code, journal.EXIT_ABGELEHNT, out)
        self.assertIn("frei sind nur 199,00", out)
        self.assertNotIn("BBB", self.remote_ledger())

    def test_meldung_nach_push_enthaelt_andere_sitzungen(self):
        b = self.klon("b")
        self.kauf(self.a, symbol="AAA", stueck="1", kurs="100")
        sh(b, "pull", "-q")
        self.kauf(b, strategie="spekulativ", symbol="BBB", stueck="1", kurs="50")
        code, out = self.verkauf(self.a, symbol="AAA", kurs="110")  # a ist veraltet
        self.assertEqual(code, 0, out)
        self.assertIn("Frei f\u00fcr neue K\u00e4ufe: 449,00", out)

    def test_echter_konflikt_wird_erkannt_und_nichts_überschrieben(self):
        b = self.klon("b")
        for verz, wert in ((self.a, "400.00"), (b, "300.00")):
            pfad = os.path.join(verz, journal.SETTINGS)
            d = json.loads(lies(pfad))
            d["startkapital_eur"] = wert
            with open(pfad, "w") as fh:
                json.dump(d, fh)
        self.assertEqual(run(["sichern", "--dir", self.a])[0], 0)
        b_commit_vorher = sh(b, "rev-parse", "HEAD")
        code, out = run(["sichern", "--dir", b])
        self.assertEqual(code, journal.EXIT_KONFLIKT, out)
        self.assertIn("Konflikt", out)
        remote = subprocess.run(["git", "--git-dir", self.remote, "show", "main:" + journal.SETTINGS],
                                stdout=subprocess.PIPE, universal_newlines=True).stdout
        self.assertIn("400.00", remote)
        self.assertNotEqual(sh(b, "rev-parse", "HEAD"), b_commit_vorher)  # lokaler Commit angelegt
        self.assertIn("300.00", sh(b, "show", "HEAD:" + journal.SETTINGS))  # und erhalten

    def test_niemals_force_push(self):
        src = lies(os.path.join(ROOT, "trader-journal", "journal.py"))
        for verboten in ("--force", "\"-f\"", "+HEAD", "reset\", \"--hard", "push.default"):
            self.assertNotIn(verboten, src)

    def test_sichern_ohne_aenderungen_ist_still_im_hook(self):
        code, out = run(["sichern", "--dir", self.a, "--hook"])
        self.assertEqual((code, out), (0, ""))


class Rechnen(Basis):
    def test_fifo_mit_gebuehren(self):
        self.kauf(self.a, stueck="2", kurs="100", gebuehren="1", zeit="2026-11-17 10:00")
        self.kauf(self.a, stueck="1", kurs="110", gebuehren="1", zeit="2026-11-17 11:00")
        code, out = self.verkauf(self.a, stueck="2", kurs="120", gebuehren="1")
        self.assertEqual(code, 0, out)
        e = self.ergebnis(self.a)
        v = e.verkaeufe["kurzfristig"][0]
        self.assertEqual(v["kosten"], Decimal("201"))
        self.assertEqual(v["erloes"], Decimal("239"))
        self.assertEqual(v["ergebnis"], Decimal("38"))
        self.assertEqual(v["gebuehren"], Decimal("2"))
        self.assertEqual(e.gebunden, Decimal("111"))
        self.assertEqual(e.frei, Decimal("389"))  # Gewinne erhöhen das Budget nicht
        self.assertEqual(e.bargeld, Decimal("427"))
        self.assertIn("+38,00 €", out)

    def test_verlust_verringert_das_budget(self):
        self.kauf(self.a, kurs="100", gebuehren="1")
        self.verkauf(self.a, kurs="90", gebuehren="1")
        e = self.ergebnis(self.a)
        self.assertEqual(e.realisiert, Decimal("-12"))
        self.assertEqual(e.frei, Decimal("488"))

    def test_strategien_bleiben_getrennt(self):
        self.kauf(self.a, strategie="kurzfristig", symbol="XYZ")
        code, out = self.verkauf(self.a, strategie="spekulativ", symbol="XYZ")
        self.assertEqual(code, journal.EXIT_ABGELEHNT)
        self.assertIn("nichts gespeichert", out)
        e = self.ergebnis(self.a)
        self.assertIn("XYZ", e.offen["kurzfristig"])
        self.assertEqual(e.offen["spekulativ"], {})

    def test_kauf_ueber_budget_wird_abgelehnt(self):
        vorher = self.remote_ledger()
        code, out = self.kauf(self.a, stueck="6", kurs="100")
        self.assertEqual(code, journal.EXIT_ABGELEHNT)
        self.assertIn("Abgelehnt", out)
        self.assertEqual(self.remote_ledger(), vorher)
        self.assertEqual(lies(os.path.join(self.a, journal.LEDGER)), vorher)

    def test_ueber_budget_nur_mit_bestaetigung(self):
        code, out = self.kauf(self.a, "--ueber-budget-bestaetigt", stueck="6", kurs="100")
        self.assertEqual(code, 0, out)
        self.assertIn("WARNUNG", out)
        self.assertLess(self.ergebnis(self.a).frei, 0)

    def test_r_vielfaches(self):
        self.kauf(self.a, "--stop", "98", kurs="100", gebuehren="0")
        self.verkauf(self.a, kurs="104", gebuehren="0")
        self.assertEqual(self.ergebnis(self.a).verkaeufe["kurzfristig"][0]["r"], Decimal(2))

    def test_stop_ueber_kurs_wird_abgelehnt(self):
        code, out = self.kauf(self.a, "--stop", "101", kurs="100")
        self.assertEqual(code, journal.EXIT_ABGELEHNT)

    def test_storno(self):
        self.kauf(self.a)
        i = journal.eintraege(self.a)[-1]["id"]
        code, out = run(["storno", "--dir", self.a, "--id", i, "--grund", "Testfehler"])
        self.assertEqual(code, 0, out)
        self.assertEqual(self.ergebnis(self.a).offen["kurzfristig"], {})
        self.assertEqual(len(journal.eintraege(self.a)), 2)  # Original bleibt erhalten

    def test_deutsche_zahlen(self):
        self.assertEqual(journal.betrag("318,18"), Decimal("318.18"))
        self.assertEqual(journal.betrag("1.234,56"), Decimal("1234.56"))
        self.assertEqual(journal.betrag("0.99"), Decimal("0.99"))
        self.assertEqual(journal.eur(Decimal("1234.5")), "1.234,50 €")
        self.assertEqual(journal.eur(Decimal("-3.2")), "-3,20 €")
        with self.assertRaises(journal.JournalFehler):
            journal.betrag("-1")

    def test_deutsche_zeit(self):
        import datetime as dt
        utc = dt.timezone.utc
        self.assertEqual(journal.deutsche_zeit(dt.datetime(2026, 11, 17, 18, 0, tzinfo=utc))[1], "MEZ")
        self.assertEqual(journal.deutsche_zeit(dt.datetime(2026, 7, 1, 18, 0, tzinfo=utc))[0].hour, 20)


class Berichte(Basis):
    def test_bericht_deutsch_und_getrennt(self):
        self.kauf(self.a, strategie="spekulativ", symbol="SPEC", kurs="50")
        self.kauf(self.a, symbol="KURZ")
        self.verkauf(self.a, symbol="KURZ", kurs="105")
        code, out = run(["bericht", "--dir", self.a, "--mit-fiktiven", "--kurs", "SPEC=40,00"])
        self.assertEqual(code, 0)
        for teil in ("## Kurzfristiges Trading", "## Spekulative Positionen", "Totalverlust",
                     "Trefferquote", "nicht realisiert -11,00 €", "FIKTIVE", "keine Anlageberatung"):
            self.assertIn(teil, out)

    def test_fiktive_daten_zaehlen_nicht_in_echten_auswertungen(self):
        self.kauf(self.a)
        code, out = run(["status", "--dir", self.a])
        self.assertIn("Frei für neue Käufe     500,00 €", out)
        self.assertIn("Noch keine echten Ausführungen", out)


class NichtGeladen(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._env = dict(os.environ)
        os.environ.pop("TRADER_JOURNAL_DIR", None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ohne_tagebuch_keine_zahlen(self):
        for cmd in (["status"], ["bericht"], ["liste"]):
            code, out = run(cmd + ["--dir", os.path.join(self.tmp, "fehlt")])
            self.assertEqual(code, journal.EXIT_NICHT_GELADEN, cmd)
            self.assertIn("NICHT GELADEN", out)
            self.assertNotIn("€", out)

    def test_kauf_ohne_tagebuch_wird_nicht_erfasst(self):
        code, out = run(["kauf", "--dir", os.path.join(self.tmp, "fehlt"), "--strategie", "kurzfristig",
                         "--symbol", "X", "--stueck", "1", "--kurs", "1", "--gebuehren", "0"])
        self.assertEqual(code, journal.EXIT_NICHT_GELADEN)


class Start(Basis):
    def setUp(self):
        super(Start, self).setUp()
        self.kauf(self.a, symbol="ALT")
        self.projekt = os.path.join(self.tmp, "projekt")
        os.makedirs(self.projekt)
        self.env_file = os.path.join(self.tmp, "env")

    def start(self, repo, verz):
        os.environ["CLAUDE_ENV_FILE"] = self.env_file
        return run(["start", "--projekt", self.projekt, "--repo", repo, "--dir", verz])

    def test_neustart_laedt_tagebuch_und_verlinkt_trader(self):
        verz = os.path.join(self.tmp, "trading-journal")
        code, out = self.start(self.remote, verz)
        self.assertEqual(code, 0)
        self.assertIn("Tagebuch geladen", out)
        self.assertTrue(os.path.islink(os.path.join(self.projekt, "trader")))
        self.assertEqual(os.path.realpath(os.path.join(self.projekt, "trader")),
                         os.path.realpath(os.path.join(verz, "trader")))
        self.assertIn("TRADER_JOURNAL_DIR=%s" % verz, lies(self.env_file))
        self.assertIn("ALT", json.dumps(journal.eintraege(verz)))
        code, out = self.start(self.remote, verz)  # zweiter Start: aktualisieren statt klonen
        self.assertEqual(code, 0)
        self.assertIn("Tagebuch geladen", out)

    def test_start_sichert_vorher_ungesichertes(self):
        verz = os.path.join(self.tmp, "trading-journal")
        self.start(self.remote, verz)
        with open(os.path.join(verz, "trader", "config.json"), "w") as fh:
            fh.write("{}\n")
        code, out = self.start(self.remote, verz)
        self.assertEqual(code, 0)
        self.assertIn("trader/config.json", sh(self.remote, "ls-tree", "-r", "--name-only", "main"))

    def test_klonfehler_meldet_nicht_geladen_ohne_zahlen(self):
        code, out = self.start(os.path.join(self.tmp, "gibt-es-nicht.git"), os.path.join(self.tmp, "tj"))
        self.assertEqual(code, 0)  # Sitzung startet trotzdem
        self.assertIn("TAGEBUCH NICHT GELADEN", out)
        self.assertIn("add_repo", out)
        self.assertNotIn("€", out)
        self.assertFalse(os.path.lexists(os.path.join(self.projekt, "trader")))

    def test_echter_trader_ordner_wird_nicht_ersetzt(self):
        os.makedirs(os.path.join(self.projekt, "trader"))
        code, out = self.start(self.remote, os.path.join(self.tmp, "trading-journal"))
        self.assertIn("NICHT ersetzt", out)
        self.assertFalse(os.path.islink(os.path.join(self.projekt, "trader")))


class ÖffentlichesRepository(unittest.TestCase):
    def test_trader_symlink_und_ordner_sind_ignoriert(self):
        for pfad in ("trader", "trader/config.json"):
            p = subprocess.run(["git", "-C", ROOT, "check-ignore", "-q", pfad])
            self.assertEqual(p.returncode, 0, pfad)

    def test_keine_handelsdaten_versioniert(self):
        p = subprocess.run(["git", "-C", ROOT, "ls-files"], stdout=subprocess.PIPE, universal_newlines=True)
        for f in p.stdout.splitlines():
            self.assertFalse(f == "trader" or f.startswith("trader/"), f)
            self.assertFalse(f.endswith(".jsonl"), f)

    def test_hooks_sind_registriert(self):
        with open(os.path.join(ROOT, ".claude", "settings.json")) as fh:
            hooks = json.load(fh)["hooks"]
        self.assertIn("session-start.sh", json.dumps(hooks["SessionStart"]))
        self.assertIn("stop.sh", json.dumps(hooks["Stop"]))
        for name in ("session-start.sh", "stop.sh"):
            pfad = os.path.join(ROOT, ".claude", "hooks", name)
            self.assertTrue(os.access(pfad, os.X_OK), name)
            self.assertIn('CLAUDE_CODE_REMOTE', lies(pfad))


if __name__ == "__main__":
    unittest.main()
