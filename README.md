# TVSpielfilm für OpenATV / Enigma2

TVSpielfilm ist eine elektronische Programmzeitschrift für Enigma2.

- **TV-Magazin und flexible Zeiträume:** Sendungen lassen sich im Magazin oder in Übersichten für „Jetzt im TV“, die Primetime und selbst konfigurierte Zeiträume anzeigen. Zeiträume A bis E können jeweils aktiviert und mit Startzeit und Dauer eingestellt werden.
- **Senderübersicht nach Kategorien:** Importierte Sender sind in Kategorien abrufbar. Beim Import werden Enigma2-Bouquets den TVSpielfilm-Sendern zugeordnet; die Zuordnungsregeln können geprüft und aktualisiert werden.
- **Erweiterte Sendungsdetails:** Detailseiten zeigen unter anderem Beschreibung, Besetzung und Crew, FSK, Sender, Laufzeit, Bewertungen und Kennzeichnungen wie Tipp, Neu oder Live. IMDb- und TMDb-Suche sowie verfügbare Trailerinformationen werden unterstützt.
- **Tipps des Tages:** Die Tipps können beim Pluginstart oder fortlaufend eingeblendet werden. Bilder und Tippdaten werden lokal zwischengespeichert.
- **Steuerbares EPG-Update:** Das manuelle Update kann fehlende Daten ergänzen oder vorhandene Daten für heute beziehungsweise vollständig erneuern. Beim Ergänzen werden bereits vorhandene Senderdaten übersprungen. Ein automatisches Update lässt sich über die Aufgabenplanung des Images starten und abbrechen.
- **Konfigurierbarer Cache:** Cache-Pfad, Anzahl vorausgeladener zukünftiger Tage und Aufbewahrungsdauer alter Daten sind einstellbar. Veraltete Cache-Dateien werden bereinigt; bei Bedarf kann der Cache im Plugin gelöscht werden.
- **Begrenztes Vorabladen von Details:** Sendungsdetails und Bilder können parallel geladen werden. Die Zahl gleichzeitiger Detail-Downloads ist einstellbar oder abschaltbar.
- **Mehr Anzeigeoptionen:** Sendernamen können vom Image oder vom TVSpielfilm-Server stammen. Picon-Quelle, Menüeinträge, Tipps und Datenbank-Präferenz für Detailinformationen lassen sich konfigurieren.

## Bedienung

Im Hauptmenü stehen TV-Magazin, konfigurierte Zeiträume, „Jetzt im TV“, die laufende Sendung, die Senderübersicht sowie EPG-Update und Cache-Löschen zur Verfügung. Sendungen können aus den Übersichten oder der Detailansicht direkt als Enigma2-Timer angelegt werden. Bereits vorhandene Timer werden in den Übersichten und Details gekennzeichnet.

Der Senderimport muss vor dem ersten EPG-Update eingerichtet werden. Nach einem Import empfiehlt sich ein Update, damit die Programmdaten für die ausgewählten Sender geladen werden.

## Einstellungen

Die Plugin-Einstellungen sind über die Menütaste im Plugin erreichbar. Im Expertenmodus stehen zusätzlich unter anderem folgende Optionen bereit:

| Bereich | Optionen |
| --- | --- |
| Daten | Cache-Pfad, zukünftige Cache-Tage, Aufbewahrung alter Daten |
| Update | Verhalten beim Aktualisieren vorhandener Daten, automatische Aktualisierung |
| Vorabladen | Parallele Sendungsdetails-Downloads: aus, 2, 4 oder 8 |
| Zeiträume | A bis E aktivieren sowie Startzeit und Dauer festlegen |
| Anzeige | Sendernamen, Picons, Tipps und Menüeinträge konfigurieren |
| Details | IMDb- oder TMDb-Suche bevorzugen |

## Voraussetzungen

- Enigma2-Receiver mit OpenATV oder kompatiblem Image
- Netzwerkzugriff auf den TVSpielfilm-Dienst für aktuelle EPG- und Detaildaten
- Ein dauerhaft verfügbarer Speicherort für den Cache wird empfohlen

Beim Pluginstart wird der Zugriff auf TVSpielfilm und den Bildserver geprüft. Ist der Zugriff nicht möglich, erscheint eine Fehlermeldung und die Menüpunkte bleiben gesperrt. Prüfen Sie in diesem Fall die Internetverbindung und gegebenenfalls den VPN-Server.
