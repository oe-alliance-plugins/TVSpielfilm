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
from datetime import datetime
from os.path import isfile
from twisted.internet.threads import deferToThread

# ENIGMA IMPORTS
from Components.config import config
from Plugins.Plugin import PluginDescriptor
from ServiceReference import ServiceReference
from Screens.MessageBox import MessageBox

# PLUGIN IMPORTS
from .tvglobals import tvglobals


def start_epg_autoupdate(timer_entry):
	from .tvautoupdate import tvautoupdate
	return tvautoupdate.autoupdate_epg(timer_entry)


def stop_epg_autoupdate(*args, **kwargs):
	setattr(tvglobals, "AUTOUPDATESTOP", True)


def show_current_program(session, **kwargs):
	from .tvcorehelper import tvcorehelper
	from .ui import TVFullscreen

	def show(asset_url):
		if asset_url:
			session.open(TVFullscreen, asset_url, datetime.now())
		else:
			s_ref = session.nav.getCurrentlyPlayingServiceReference().toString()
			s_name = ServiceReference(s_ref).getServiceName()
			session.open(MessageBox, f"Sender '{s_name}' wird vom TV Spielfilm Server nicht unterstützt.", type=MessageBox.TYPE_INFO, timeout=2, close_on_any_key=True)

	deferToThread(tvcorehelper.get_current_asset_url, session.nav.getCurrentlyPlayingServiceOrGroup()).addCallback(show)  # keep the web request out of the GUI thread


def show_nowontv(session, **kwargs):
	from .ui import TVImport, TVOverview
	if isfile(tvglobals.IMPORTFILE):
		session.open(TVOverview, (("", "now"), config.plugins.tvspielfilm.durance_n.value))
	else:
		session.open(TVImport)


def show_primetime(session, **kwargs):
	from .tvcorehelper import tvcorehelper
	from .ui import TVImport, TVOverview
	if isfile(tvglobals.IMPORTFILE):
		starting, duration = next(((starting, duration) for starting, duration in tvcorehelper.get_user_timespans() if starting[0] == "20:15"), (None, None))
		if starting and duration:
			session.open(TVOverview, (starting, duration))
		else:
			session.open(MessageBox, "Es wurde kein aktivierter Zeitraum '20:15' in den TVS-Usersettings gefunden.\nBitte im TVS-Setup einen Zeitraum mit '20:15' belegen und aktivieren.", type=MessageBox.TYPE_INFO, timeout=2, close_on_any_key=True)
	else:
		session.open(TVImport)


def main(session, **kwargs):
	from .ui import TVImport, TVMain
	if isfile(tvglobals.IMPORTFILE):
		session.open(TVMain)
	else:
		session.open(TVImport)


def sessionstart(reason, session):
	if reason == 0:
		try:  # in case image doesn't support Scheduler
			from Scheduler import addFunctionTimer
			addFunctionTimer("TVAutoUpdate", "TVSpielfilm-EPG Datenupdate", start_epg_autoupdate, stop_epg_autoupdate, useOwnThread=False)
		except ImportError as err_msg:
			print(f"[{tvglobals.MODULE_NAME}] INFO: This image doesn't support autotimer module 'Scheduler.addFunctionTimer': {err_msg}")


def autostart(reason, **kwargs):
	pass


def Plugins(**kwargs):
	pd_list = [
		PluginDescriptor(name="TV Spielfilm", icon=f"pics/{tvglobals.RESOLUTION}/logos/plugin.png", description="Elektronische Programmzeitschrift mit umfangreichen Zusatzinfos", where=PluginDescriptor.WHERE_PLUGINMENU, fnc=main),
		PluginDescriptor(where=PluginDescriptor.WHERE_SESSIONSTART, needsRestart=True, fnc=sessionstart),
		PluginDescriptor(where=PluginDescriptor.WHERE_AUTOSTART, needsRestart=True, fnc=autostart)
		]
	primetime = config.plugins.tvspielfilm.primetime.value
	if primetime & 1:
		pd_list.append(PluginDescriptor(name="TV Spielfilm - Primetime 20:15", where=PluginDescriptor.WHERE_EXTENSIONSMENU, fnc=show_primetime))
	if primetime & 2:
		pd_list.append(
			PluginDescriptor(name="TV Spielfilm - Primetime 20:15", icon=f"pics/{tvglobals.RESOLUTION}/logos/TV2015.png",
			description="Zeige die laufenden und nachfolgenden Sendungen der Primetime 20:15 Uhr", where=PluginDescriptor.WHERE_PLUGINMENU, fnc=show_primetime)
		)
	if not primetime and hasattr(PluginDescriptor, 'WHERE_BUTTONSETUP'):
		pd_list.append(PluginDescriptor(name="TV Spielfilm - Primetime 20:15", where=PluginDescriptor.WHERE_BUTTONSETUP, fnc=show_primetime))
	nowontv = config.plugins.tvspielfilm.nowontv.value
	if nowontv & 1:
		pd_list.append(PluginDescriptor(name="TV Spielfilm - Jetzt im TV", where=PluginDescriptor.WHERE_EXTENSIONSMENU, fnc=show_nowontv))
	if nowontv & 2:
		pd_list.append(
			PluginDescriptor(name="TV Spielfilm - Jetzt im TV", icon=f"pics/{tvglobals.RESOLUTION}/logos/TVjetzt.png",
			description="Zeige die aktuell laufenden und demnächst anstehenden Sendungen", where=PluginDescriptor.WHERE_PLUGINMENU, fnc=show_nowontv)
		)
	if not nowontv and hasattr(PluginDescriptor, 'WHERE_BUTTONSETUP'):
		pd_list.append(PluginDescriptor(name="TV Spielfilm - Jetzt im TV", where=PluginDescriptor.WHERE_BUTTONSETUP, fnc=show_nowontv))
	if config.plugins.tvspielfilm.currprogram.value == 1:
		pd_list.append(PluginDescriptor(name="TV Spielfilm - laufende Sendung", where=PluginDescriptor.WHERE_EXTENSIONSMENU, fnc=show_current_program))
	elif hasattr(PluginDescriptor, 'WHERE_BUTTONSETUP'):
		pd_list.append(PluginDescriptor(name="TV Spielfilm - laufende Sendung", where=PluginDescriptor.WHERE_BUTTONSETUP, fnc=show_current_program))
	return pd_list
