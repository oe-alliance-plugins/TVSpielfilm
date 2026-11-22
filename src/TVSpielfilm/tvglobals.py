########################################################################################################
# TV Spielfilm by Mr.Servo @OpenATV (c) 2026 - skinned by stein17 @OpenATV                             #
# Special thanks to jbleyel @OpenATV for his valuable support in creating the code.                    #
# -----------------------------------------------------------------------------------------------------#
# This plugin is licensed under the GNU version 3.0 <https://www.gnu.org/licenses/gpl-3.0.en.html>.    #
# This plugin is NOT free software. It is open source, you are allowed to modify it (if you keep       #
# the license), but it may not be commercially distributed. Advertise with this plugin is not allowed. #
# For other uses, permission from the authors is necessary.                                            #
########################################################################################################
# PYTHON IMPORTS
from json import dumps
from os.path import join

# ENIGMA IMPORTS
from enigma import getDesktop
from Components.config import config, ConfigSubsection, ConfigSelection, ConfigYesNo, ConfigText
from Tools.Directories import resolveFilename, SCOPE_PLUGINS, SCOPE_CONFIG, SCOPE_SKIN_IMAGE, SCOPE_FONTS

# PLUGIN IMPORTS
from . import __version__
from .tvsparser import tvspassets


class TVGlobals:
	RELEASE = f"v{__version__}"
	MODULE_NAME = __name__.split(".")[-2]
	IMPORTDICT = {}
	RESOLUTION = "FHD" if getDesktop(0).size().width() > 1300 else "HD"
	CONFIGPATH = resolveFilename(SCOPE_CONFIG, "TVSpielfilm")  # e.g. '/etc/enigma2/TVSpielfilm'
	PLUGINPATH = resolveFilename(SCOPE_PLUGINS, "Extensions/TVSpielfilm")  # e.g. '/usr/lib/enigma2/python/Plugins/Extensions/TVSpielfilm'
	FONTPATH = resolveFilename(SCOPE_FONTS, "LiberationSans-Regular.ttf")  # e.g. '/usr/share/fonts/LiberationSans-Regular.ttf'
	ICONPATH = join(PLUGINPATH, f"pics/{RESOLUTION}/icons")
	IMPORTFILE = join(CONFIGPATH, "tvs_imported.json")
	SUPPFILE = join(CONFIGPATH, "tvs_supported.json")
	DUPESFILE = join(CONFIGPATH, "tvs_dupes.json")
	UNSUPPFILE = join(CONFIGPATH, "tvs_unsupported.json")
	LOGPATH = "/home/root/logs/"  # no variable path name availible in E2?
	MAPFILE = join(CONFIGPATH, "tvs_mapping.txt")
	ROWS, COLUMNS = 4, 6
	AUTOUPDATEACTIVE = False
	AUTOUPDATESTOP = False  # set to stop a running autoupdate


tvglobals = TVGlobals

STARTTIMES = (("05:00", "5"), ("08:00", "8"), ("10:00", "10"), ("12:00", "12"), ("14:00", "14"), ("16:00", "16"), ("18:00", "18"), ("20:00", "20"), ("20:15", "prime"), ("22:00", "22"), ("00:00", "0"), ("02:00", "2"), ("04:00", "4"))
STARTING = [(i, f"{x[0]} Uhr") for i, x in enumerate(STARTTIMES)]
CATFILTERS = list(tvspassets.cat_filters.items())  # [('Spielfilm', 'SP'), ('Serie', 'SE'), ('Report', 'RE'), ('Unterhaltung', 'U'), ('Kinder', 'KIN'), ('Sport', 'SPO'), ('Andere', 'AND')]
ASSETFILTERS = [("{keiner}", ""), ("Daumen", "thumb"), ("Tipp", "is_tip"), ("Neu", "is_new"), ("Live", "is_live")] + CATFILTERS  # not supported by HP: ("Tagestipp", "is_top")]
DURANCES = [(x, f"{x} Minuten") for x in range(15, 555, 15)]
VISIBILITY = [(0, "keine Anzeige"), (1, "im Extensionmenü (Taste BLAU-lang)"), (2, "im Pluginmanager (Taste GRÜN-kurz)"), (3, "in Extensionmenü und Pluginmanager")]

config.plugins.tvspielfilm = ConfigSubsection()
config.plugins.tvspielfilm.expertmode = ConfigYesNo(default=False)
config.plugins.tvspielfilm.first_item = ConfigSelection(default=0, choices=[(0, "TV Magazin"), (1, "Zeiträume")])
config.plugins.tvspielfilm.showtips = ConfigSelection(default=2, choices=[(0, "niemals"), (1, "nur bei Pluginstart"), (2, "immer")])
config.plugins.tvspielfilm.defaultfilter = ConfigText(default="0")
config.plugins.tvspielfilm.filtersettings = ConfigText(default=dumps([[x, True] for x in ASSETFILTERS]))
config.plugins.tvspielfilm.piconsource = ConfigSelection(default=0, choices=[(0, "vom Image (aus dem Standardverzeichnis)"), (1, "eigene, nur SRP (Service Reference Picons)"), (2, "eigene, nur SNP (Service Name Picons)"), (3, "eigene, SRP+SNR")])
config.plugins.tvspielfilm.piconpath = ConfigText(default=resolveFilename(SCOPE_SKIN_IMAGE, "picon/"))
config.plugins.tvspielfilm.channel_name = ConfigSelection(default=1, choices=[(0, "vom Image"), (1, "vom Server")])
config.plugins.tvspielfilm.prefered_db = ConfigSelection(default=0, choices=[(0, "jedesmal nachfragen"), (1, "IMDb - Internet Movie Database"), (2, "TMDb - The Movie Database")])
config.plugins.tvspielfilm.update_mapfile = ConfigSelection(default=1, choices=[(0, "niemals"), (1, "nach Updates")])
config.plugins.tvspielfilm.cache_path = ConfigText(default="/media/hdd/")
config.plugins.tvspielfilm.cacherange = ConfigSelection(default=7, choices=[(x, f"+{x} Tage") for x in range(1, 14)])
config.plugins.tvspielfilm.keepcache = ConfigSelection(default=7, choices=[(x, f"-{x} Tage") for x in range(8)])
config.plugins.tvspielfilm.prefetch_parallel = ConfigSelection(default=2, choices=[(0, "Aus"), (1, "Langsam (1 Download)"), (2, "Normal (2 Downloads)"), (4, "Schnell (4 Downloads)")])
config.plugins.tvspielfilm.autoupdate = ConfigSelection(default=1, choices=[(0, "ergänze nur fehlende Datensätze"), (1, "überschreibe heutige & ergänze fehlende Daten"), (2, "überschreibe alle vorhandenen Datensätze")])
config.plugins.tvspielfilm.durance_n = ConfigSelection(default=30, choices=DURANCES)  # 'Jetzt im TV'
config.plugins.tvspielfilm.use_a = ConfigYesNo(default=False)
config.plugins.tvspielfilm.starttime_a = ConfigSelection(default=2, choices=STARTING)
config.plugins.tvspielfilm.durance_a = ConfigSelection(default=120, choices=DURANCES)
config.plugins.tvspielfilm.use_b = ConfigYesNo(default=False)
config.plugins.tvspielfilm.starttime_b = ConfigSelection(default=4, choices=STARTING)
config.plugins.tvspielfilm.durance_b = ConfigSelection(default=120, choices=DURANCES)
config.plugins.tvspielfilm.use_c = ConfigYesNo(default=True)
config.plugins.tvspielfilm.starttime_c = ConfigSelection(default=8, choices=STARTING)
config.plugins.tvspielfilm.durance_c = ConfigSelection(default=105, choices=DURANCES)
config.plugins.tvspielfilm.use_d = ConfigYesNo(default=True)
config.plugins.tvspielfilm.starttime_d = ConfigSelection(default=9, choices=STARTING)
config.plugins.tvspielfilm.durance_d = ConfigSelection(default=120, choices=DURANCES)
config.plugins.tvspielfilm.use_e = ConfigYesNo(default=False)
config.plugins.tvspielfilm.starttime_e = ConfigSelection(default=10, choices=STARTING)
config.plugins.tvspielfilm.durance_e = ConfigSelection(default=120, choices=DURANCES)
config.plugins.tvspielfilm.primetime = ConfigSelection(default=1, choices=VISIBILITY)
config.plugins.tvspielfilm.nowontv = ConfigSelection(default=1, choices=VISIBILITY)
config.plugins.tvspielfilm.currprogram = ConfigSelection(default=1, choices=VISIBILITY[:2])
config.plugins.tvspielfilm.mapfilehash = ConfigText(default="")
