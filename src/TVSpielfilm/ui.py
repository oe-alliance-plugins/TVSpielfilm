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
from datetime import datetime, timedelta
from html.parser import HTMLParser
from json import load, loads, dump, dumps
from os import rename
from os.path import isfile, join
from re import compile, match, sub
from threading import Lock
from twisted.internet import reactor
from twisted.internet.reactor import callInThread, callFromThread
from twisted.internet.threads import blockingCallFromThread, deferToThread
from twisted.python.failure import Failure
from unicodedata import normalize
from urllib.parse import urlsplit

# ENIGMA IMPORTS
from enigma import eServiceReference, eServiceCenter, eTimer, addFont
from Components.ActionMap import ActionMap, HelpableActionMap
from Components.config import config
from Components.Pixmap import Pixmap
from Components.ProgressBar import ProgressBar
from Components.Renderer.Picon import getPiconName
from Components.ScrollLabel import ScrollLabel
from Components.Sources.List import List
from Components.Sources.ServiceList import ServiceList
from Components.Sources.StaticText import StaticText
from ServiceReference import ServiceReference
from Screens.ChoiceBox import ChoiceBox
from Screens.InfoBar import MoviePlayer
from Screens.LocationBox import defaultInhibitDirs, LocationBox
from Screens.MessageBox import MessageBox
from Screens.Screen import Screen
from Screens.Setup import Setup
from Tools.Directories import isPluginInstalled
from Tools.LoadPixmap import LoadPixmap

# PLUGIN IMPORTS
from .tvcorehelper import TVCoreHelper
from .tvglobals import tvglobals
from .tvsparser import tvsptips, tvspchannels, tvspassets, tvsphelper, tvspglobals

TVS_UPDATEACTIVE, TVS_UPDATESTOP = False, False
TVS_REGISTERED_FONTS = set()


class TVScreenHelper(TVCoreHelper, Screen):
	def call_gui(self, callback, *args, **kwargs):
		return blockingCallFromThread(reactor, callback, *args, **kwargs)

	def update_widget(self, widget_name, method_name, *args):
		return self.call_gui(lambda: getattr(self[widget_name], method_name)(*args))

	def init_asset_prefetch(self):
		self.prefetch_queue = []
		self.prefetch_running = set()
		self.prefetch_targets = set()
		self.prefetch_total = 0
		self.prefetch_completed = 0
		self.prefetch_in_progress = 0
		self.prefetch_paused = False
		self.prefetch_active = False

	def start_asset_prefetch(self, asset_urls):
		callFromThread(self._start_asset_prefetch, list(asset_urls))

	def abort_asset_prefetch(self):
		self.key_exit_pressed = True
		self.prefetch_queue.clear()
		self.prefetch_targets.clear()
		self.prefetch_paused = False
		self.close(False)

	def _start_asset_prefetch(self, asset_urls):
		self.prefetch_targets = set(asset_urls) if config.plugins.tvspielfilm.prefetch_parallel.value and not self.key_exit_pressed else set()
		self.prefetch_queue = [asset_url for asset_url in dict.fromkeys(asset_urls) if asset_url in self.prefetch_targets and asset_url not in self.prefetch_running]
		self.prefetch_total = len(self.prefetch_targets)
		self.prefetch_completed = 0
		if self.prefetch_targets or self.prefetch_running:
			self.prefetch_active = True
			if self.prefetch_targets:
				self["prefetchpic"].show()
				self["prefetchbar"].setRange((0, self.prefetch_total))
				self["prefetchbar"].setValue(0)
				self["prefetchbar"].show()
			self.pump_asset_prefetch()
		else:
			self.finish_asset_prefetch()

	def pump_asset_prefetch(self):
		if self.key_exit_pressed:
			self.prefetch_queue.clear()
			self.prefetch_paused = False
		if self.prefetch_paused:
			return
		max_parallel = config.plugins.tvspielfilm.prefetch_parallel.value
		while self.prefetch_queue and self.prefetch_in_progress < max_parallel:
			asset_url = self.prefetch_queue.pop(0)
			if isfile(self.single_asset_filename(asset_url)):
				self.prefetch_completed += 1
				self["prefetchbar"].setValue(self.prefetch_completed)
				continue
			self.prefetch_in_progress += 1
			self.prefetch_running.add(asset_url)
			deferToThread(self.get_single_asset, asset_url).addBoth(self.asset_prefetch_done, asset_url)
		if not self.prefetch_queue and not self.prefetch_in_progress:
			self.finish_asset_prefetch()

	def asset_prefetch_done(self, result, asset_url):
		self.prefetch_in_progress -= 1
		self.prefetch_running.discard(asset_url)
		if asset_url in self.prefetch_targets:
			self.prefetch_completed += 1
		if isinstance(result, Failure):
			print(f"[{self.MODULE_NAME}] Asset prefetch failed: {result.getErrorMessage()}")
		if not self.key_exit_pressed and self.prefetch_targets:
			self["prefetchbar"].setValue(self.prefetch_completed)
		self.pump_asset_prefetch()

	def finish_asset_prefetch(self):
		self.prefetch_active = False
		if self.key_exit_pressed:
			return
		self["prefetchpic"].hide()
		self["prefetchbar"].hide()

	def pause_asset_prefetch(self, paused):
		prefetch_owner = getattr(self, "prefetch_owner", None) or self
		if getattr(prefetch_owner, "prefetch_active", False):
			prefetch_owner.prefetch_paused = paused
			if not paused:
				prefetch_owner.pump_asset_prefetch()

	def image_download(self, img_url, callback=None, asset_url="", column=0):
		err_msg, img_file = self.cache_image(img_url)
		if err_msg:
			print(f"[{self.MODULE_NAME}] ERROR in class 'TVScreenHelper:image_download': {img_url} - picture could not be saved: {err_msg}")
		elif callback and img_file:
			callFromThread(callback, img_file, asset_url, column)

	def show_asset_details(self, asset_url, full_screen=False):
		err_msg, asset = self.get_single_asset(asset_url)
		callFromThread(self.show_asset_details_ui, asset_url, full_screen, err_msg, asset)

	def show_asset_details_ui(self, asset_url, full_screen, err_msg, asset):
		if err_msg:
			print(f"[{self.MODULE_NAME}] ERROR in class 'TVScreenHelper:show_asset_details_ui' - getting single asset failed: {err_msg}")
		if asset and asset_url == self.curr_asset_url:  # show if asset_url is still active
			timestart_iso = asset.get("time_start", "")
			try:
				self.timestart_dt = datetime.fromisoformat(timestart_iso).replace(tzinfo=None) if timestart_iso else datetime.now(tz=None)
			except (TypeError, ValueError):
				self.timestart_dt = datetime.now(tz=None)
			timeend_iso = asset.get("time_end", "")
			try:
				timeend_dt = datetime.fromisoformat(timeend_iso).replace(tzinfo=None) if timeend_iso else datetime.now(tz=None)
			except (TypeError, ValueError):
				timeend_dt = datetime.now(tz=None)
			timestart_str = self.timestart_dt.strftime("%H:%M")
			timestartend_str = f"{timestart_str} - {timeend_dt.strftime('%H:%M')}"
			timestartend_ts = (int(self.timestart_dt.timestamp()), int(timeend_dt.timestamp()))
			repeat_hint = asset.get("repeat_hint", "")  # e.g.'Wh. um 00:20 Uhr, Nächste Episode um 21:55 Uhr (Staffel 8, Episode 24)'
			channel_id = asset.get("channel_id", "").lower()
			channel_name = asset.get("channel_name", "") if config.plugins.tvspielfilm.channel_name.value else self.IMPORTDICT.get(channel_id, ["", ""])[1]
			subline = asset.get("preview", "")
			subline += "\n" if subline else ""
			season_number = asset.get("season_number", "")
			season_number = f"S{season_number}" if season_number else ""
			episode_number = asset.get("episode_number", "")
			episode_number = f"E{episode_number}" if episode_number else ""
			season_episode = " | ".join(list(filter(None, [season_number, episode_number])))
			conclusion = asset.get("conclusion", "")
			text = asset.get("text", "").replace("\n\n", "\n")
			self.asset_title = asset.get("title", "") or asset.get("episode_title", "")
			thumb_id_numeric = asset.get("thumb_id_numeric", 0)
			thumb_id_numeric = 4 - thumb_id_numeric if thumb_id_numeric else 0
			demanding = asset.get("ratingDemanding")
			humor = asset.get("ratingHumor")
			action = asset.get("ratingAction")
			suspense = asset.get("ratingSuspense")
			erotic = asset.get("ratingErotic")
			genre = asset.get("genre", "")  # e.g. 'Katastrophenaction'
			program_type = {v: k for k, v in tvspassets.cat_filters.items()}.get(asset.get("category", "AND"), "")  # e.g. 'SP' for 'Spielfilm'
			img_url = asset.get("img_url", "")
			if img_url and (img_file := self.image_filename(img_url)):
				if isfile(img_file):
					self["image"].instance.setPixmapFromFile(img_file)
					self["image"].show()
				else:  # download, save & set very first image immediately (if this tip_url is still up to date by then)
					callInThread(self.image_download, img_url, callback=self.set_asset_image, asset_url=asset_url)
			img_credits = asset.get("img_credits", "")
			self.trailer_data = asset.get("trailer_data", "")
			anchorman = asset.get("anchorman", "")
			anchorman = f"mit {anchorman}\n" if anchorman else ""
			current_topics = asset.get("current_topics", "")
			current_topics += "\n" if current_topics else ""
			first_year = asset.get("first_year", "")
			country = asset.get("country", "")
			country_year = f"{country} {first_year}".strip()
			imdb_rating = asset.get("imdb_rating", "")
			fsk = asset.get("fsk", "")
			fsk_text = f"ab {fsk} Jahren" if fsk and fsk > -1 else ""
			persons = "\n"
			for member in asset.get("crew", {}).items():
				persons += f"{member[0]}:\t{', '.join(member[1])}\n"
			for index, member in enumerate(asset.get("cast", {}).items()):
				if not index:  # if very first member
					persons += "\nDarsteller:\n"
				persons += f"{', '.join(member[1])} als '{member[0]}'\n"
			sref = self.IMPORTDICT.get(channel_id, ["", ""])[0]
			has_timer = self.is_already_listed(timestartend_ts, sref) if timestartend_ts and sref else False
			picon_file = self.get_picon_file(channel_id)
			if picon_file and isfile(picon_file):
				self["picon"].instance.setPixmapFromFile(picon_file)
				self["picon"].show()
			else:
				self["picon"].hide()
			self["channel_name"].setText(channel_name)
			for widget in ["is_top", "is_tip", "is_new", "is_live", "has_timer", "is_imdb", "is_tmdb"]:
				if asset.get(widget):
					self[widget].show()
				else:
					self[widget].hide()
			if self.trailer_data:
				self["playbutton"].show()
				self["playpic"].show()
				self["key_play"].setText("Trailer abspielen")
			else:
				self["playbutton"].hide()
				self["playpic"].hide()
				self["key_play"].setText("")
			iconpath = self.ICONPATH
			for index, (rating, category) in enumerate([
				(humor, "Humor"), (demanding, "Anspruch"), (action, "Action"),
				(suspense, "Spannung"), (erotic, "Erotik")
				]):
				self[f"ratinglabel{index}l"].setText(category if rating is None else "")
				self[f"ratinglabel{index}h"].setText("" if rating is None else category)
				picfile = join(iconpath, "pointbar_.png") if rating is None else join(iconpath, f"pointbar{rating}.png")
				if isfile(picfile):
					self[f"ratingdots{index}"].instance.setPixmapFromFile(picfile)
					self[f"ratingdots{index}"].show()
				else:
					self[f"ratingdots{index}"].hide()
			self["imdb_rating"].setText(f"IMDB-Wertung: {imdb_rating}" if imdb_rating else "")
			if thumb_id_numeric:
				thumbfile = join(iconpath, f"thumb{thumb_id_numeric}.png")
				if isfile(thumbfile):
					self["thumb"].instance.setPixmapFromFile(thumbfile)
					self["thumb"].show()
			else:
				self["thumb"].hide()
			fskfile = join(iconpath, f"FSK_{fsk}.png") if fsk and fsk > -1 else ""
			if fskfile:
				self["fsk"].instance.setPixmapFromFile(fskfile)
				self["fsk"].show()
			else:
				self["fsk"].hide()
			self["repeat_hint"].setText(repeat_hint)
			self["title"].setText(self.asset_title)
			self["editorial"].setText("Meinung der Redaktion:" if conclusion else "")
			self["conclusion"].setText(conclusion)
			self["long_description"].setText(f"{subline}{current_topics}{anchorman}{text}\n{persons}")
			self["key_green"].setText("" if has_timer else "Timer")
			self.databases = self.possible_search_dbs(asset)
			self["key_info"].setText(self.get_search_db_descs(self.databases))
			self.curr_service_ref = sref
			if full_screen:
				start_str, end_str = timestartend_str.split(" - ")
				titlelen_str = f"{int((datetime.strptime(end_str, '%H:%M') - datetime.strptime(start_str, '%H:%M')).seconds / 60)} Minuten"
				dateonly_dt = self.curr_date_dt.date()
				now_dateonly_dt = datetime.now(tz=None).date()
				day_names = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
				weekday = "heute" if now_dateonly_dt == dateonly_dt else day_names[dateonly_dt.weekday()]
				timestartend_str = f"{weekday} | {now_dateonly_dt.strftime('%d.%m.%Y')} | {timestartend_str}"
				for index, (label, text) in enumerate([("Staffel/Episode", season_episode), ("Genre", genre), ("Typ", program_type),
														("Altersfreigabe", fsk_text), ("Land | Jahr", country_year), ("Länge", titlelen_str)]):
					if text:
						self[f"type_label{index}h"].setText(label)
						self[f"type_label{index}l"].setText("")
						self[f"type_text{index}"].setText(text)
					else:
						self[f"type_label{index}h"].setText("")
						self[f"type_label{index}l"].setText(label)
						self[f"type_text{index}"].setText("")
				self["credits"].setText(img_credits)
			self["timestartend"].setText(timestartend_str)
			self.timestartend_str = timestartend_str
			self.subline = subline

	def hide_asset_details(self):
		for widget in ["picon", "thumb", "image", "playbutton"]:
			self[widget].hide()
		for asset_flag in ["is_top", "is_new", "is_tip", "is_live", "has_timer", "is_imdb", "is_tmdb", "fsk"]:
			self[asset_flag].hide()
		for index in range(len(["Anspruch", "Humor", "Action", "Spannung", "Erotik"])):
				self[f"ratinglabel{index}l"].setText("")
				self[f"ratinglabel{index}h"].setText("")
				self[f"ratingdots{index}"].hide()
		for widget in ["channel_name", "imdb_rating", "repeat_hint", "title", "editorial", "conclusion", "timestartend", "long_description"]:
			self[widget].setText("")

	def set_asset_image(self, img_file, asset_url, column=0):  # column is obligatory due compatibility reasons
		if isfile(img_file) and asset_url == self.curr_asset_url:  # show if no other asset was selected in the meantime
			self["image"].instance.setPixmapFromFile(img_file)
			self["image"].show()

	def get_picon_file(self, channel_id):
		def get_clean_filename(value):
			#   Converts to lowercase, removes non-word characters (alphanumerics and underscores) and converts spaces to hyphens.
			#	Also strips leading and trailing whitespace. Function is from django
			#	https://github.com/django/django/blob/9108696a7553123f57c5d42f9c4a90cad44532f4/django/utils/text.py#L417
			value = normalize('NFKD', value)
			value = sub("[+]", "plus", value)
			value = sub("[&]", "and", value)
			value = sub("[^\\w\\s-]", "", value).strip().lower()
			value = sub("[-\\s]+", "-", value)
			return value

		import_dict = self.IMPORTDICT
		sref = org_sref = import_dict.get(channel_id, ["", ""])[0]
		fallback = sref.split(":")  # fallback from "1:0:*:..." to "1:0:1:..."
		if len(fallback) > 1:
			fallback[2] = "1"
			fallback = ":".join(fallback)
			fallback = (f"{fallback}FIN").replace(":", "_").replace("_FIN", "").replace("FIN", "")
		sref = f"{sref}FIN".replace(":", "_").replace("_FIN", "").replace("FIN", "")
		picon_source = config.plugins.tvspielfilm.piconsource.value
		if not picon_source:  # search olny for the image Service Reference Picons (SRPs)
			picon_file = getPiconName(org_sref)
			return picon_file if isfile(picon_file) else ""
		if picon_source & 1:  # search for Service Reference Picons (SRPs)
			for picons_ref in [sref, fallback]:
				picon_file = join(config.plugins.tvspielfilm.piconpath.value, f"{picons_ref}.png")
				if isfile(picon_file):
					return picon_file
		if picon_source & 2:  # search for Service Name Picons (SNPs)
			picon_name = get_clean_filename(import_dict.get(channel_id, ["", ""])[1])
			picon_file = join(config.plugins.tvspielfilm.piconpath.value, f"{picon_name}.png")
			if isfile(picon_file):
				return picon_file
		return ""

	def split_timespan(self, timespan, current_dt):
		startstr, endstr = timespan  # e.g. ("20:15", "22:45")
		starthour, startminute = startstr.split(":")
		endhour, endminute = endstr.split(":")
		start_ts = int(datetime.timestamp(current_dt.replace(hour=int(starthour), minute=int(startminute), second=0, microsecond=0)))
		end_ts = int(datetime.timestamp(current_dt.replace(hour=int(endhour), minute=int(endminute), second=0, microsecond=0)))
		return start_ts, end_ts

	def play_trailer(self):
		if self.trailer_data and "," in self.trailer_data:
			cid, lic = self.trailer_data.split(",")
			if cid and lic:
				deferToThread(tvspassets.resolve_trailer_url, cid, lic).addCallback(self.play_trailer_start)  # keep the web request out of the GUI thread

	def play_trailer_start(self, trailer_url):
		sref = eServiceReference(4097, 0, trailer_url)
		sref.setName(self.asset_title)
		self.pause_asset_prefetch(True)
		try:
			self.session.openWithCallback(self.play_trailer_cb, MoviePlayer, sref, fromMovieSelection=False)  # some images don't support this option
		except Exception:  # in case image doesn't support 'fromMovieSelection'
			self.session.openWithCallback(self.play_trailer_cb, MoviePlayer, sref)

	def play_trailer_cb(self):
		self.pause_asset_prefetch(False)

	def key_info(self):
		if self.databases == 1:
			self.open_imdb()
		elif self.databases == 2:
			self.open_tmdb()
		elif config.plugins.tvspielfilm.prefered_db.value == 1:
			self.open_imdb()
		elif config.plugins.tvspielfilm.prefered_db.value == 2:
			self.open_tmdb()
		else:
			choicelist = [
				(self.get_search_db_descs(1, longtext=True), 1),
				(self.get_search_db_descs(2, longtext=True), 2)
			]
			msgtext = "Wähle die gewünschte Datenbank:"
			self.session.openWithCallback(self.key_info_cb, ChoiceBox, list=choicelist, keys=[], windowTitle=msgtext)

	def key_info_cb(self, answer):
		if answer:
			if answer[1] == 1:
				self.open_imdb()
			elif answer[1] == 2:
				self.open_tmdb()

	def open_imdb(self):
		if isPluginInstalled("IMDb"):
			from Plugins.Extensions.IMDb.plugin import IMDB
			self.session.open(IMDB, self.asset_title, imdbId=self.curr_imdb_id)
		else:
			self.session.open(MessageBox, "Das Plugin 'IMDB' (Internet Movie DataBase) wurde nicht gefunden. Es muss zuerst installiert werden.", type=MessageBox.TYPE_WARNING, timeout=5)

	def open_tmdb(self):
		if isPluginInstalled("tmdb"):
			from Plugins.Extensions.tmdb.tmdb import tmdbScreen
			self.session.open(tmdbScreen, self.asset_title, 2)
		else:
			self.session.open(MessageBox, "Das Plugin 'TMDb' (The Movie DataBase) wurde nicht gefunden. Es muss zuerst installiert werden.", type=MessageBox.TYPE_WARNING, timeout=5)

	def open_epg_search(self):
		if isPluginInstalled("EPGSearch"):
			from Plugins.Extensions.EPGSearch.EPGSearch import EPGSearch
			self.session.open(EPGSearch, self.asset_title, False, "all")
		else:
			self.session.open(MessageBox, "Das Plugin 'EPGSearch' wurde nicht gefunden. Es muss zuerst installiert werden.", type=MessageBox.TYPE_WARNING, timeout=5)

	def zapto_current(self):
		if self.zap_allowed and self.curr_service_ref:
			self.session.nav.playService(eServiceReference(self.curr_service_ref))
			self.close(True)  # True = close complete plugin


class TVMagazine(TVScreenHelper, Screen):
	skin = """
	<screen name="TVMagazine" position="0,10" size="1280,700" resolution="1280,720" flags="wfNoBorder" backgroundColor="#16000000" transparent="0" title="TV Spielfilm Magazinsicht">
		<eLabel position="0,0" size="1280,60" backgroundColor=" black,#00203060,horizontal" zPosition="1" />
		<eLabel position="0,60" size="1280,2" backgroundColor="#0027153c,#101093,black,horizontal" zPosition="10" />
		<eLabel position="0,652" size="1280,2" backgroundColor="#0027153c,#101093,black,horizontal" zPosition="10" />
		<ePixmap position="0,0" size="220,60" pixmap="{tvs_path}/pics/{res}/logos/TVSpielfilm.png" alphatest="blend" zPosition="13" />
		<widget source="release" render="Label" position="180,28" size="80,20" font="Regular;18" textBorderColor="#00505050" textBorderWidth="1" foregroundColor="#00ffff00" backgroundColor="#16000000" valign="center" zPosition="12" transparent="1" />
		<widget source="reviewdate" render="Label" position="270,2" size="720,24" font="Regular;18" foregroundColor="white" backgroundColor="#16000000" halign="center" valign="center" zPosition="12" transparent="1" />
		<widget name="prefetchpic" position="280,16" size="96,28" alphatest="blend" zPosition="13" />
		<widget name="prefetchbar" position="290,36" size="76,2" foregroundColor="#ffff00" backgroundColor="#505050" zPosition="14" />
		<widget name="progressbar" position="450,32" size="430,24" foregroundColor="#203060" backgroundColor="#505050" transparent="1" zPosition="8" />
		<widget source="progress_txt" render="Label" position="388,34" size="60,20" font="Regular;16" foregroundColor="yellow" backgroundColor="#16000000" transparent="0" halign="center" valign="center" zPosition="9" />
		<widget source="short_status" render="Label" position="452,34" size="426,20" font="Regular;16" foregroundColor="#ffffff" transparent="1" halign="left" valign="center" wrap="ellipsis" zPosition="9" />
		<eLabel position="8,1020" size="720,22" backgroundColor="grey" zPosition="-1" />
		<widget source="long_status" render="Label" conditional="long_status" position="0,30" size="1260,26" font="Regular;16" foregroundColor="#92cbdf" backgroundColor=" black,#00203060,horizontal" halign="center" valign="center" zPosition="10">
			<convert type="ConditionalShowHide" />
		</widget>
		<widget source="global.CurrentTime" render="Label" position="1110,0" size="140,60" font="Regular;46" noWrap="1" halign="center" valign="bottom" foregroundColor="white" backgroundColor="#16000000" zPosition="12" transparent="1">
			<convert type="ClockToText">Default</convert>
		</widget>
		<widget source="global.CurrentTime" render="Label" position="1000,2" size="100,26" font="Regular;16" noWrap="1" halign="right" valign="bottom" foregroundColor="white" backgroundColor="#16000000" zPosition="12" transparent="1">
			<convert type="ClockToText">Format:%A</convert>
		</widget>
		<widget source="global.CurrentTime" render="Label" position="1000,26" size="100,26" font="Regular;16" noWrap="1" halign="right" valign="bottom" foregroundColor="white" backgroundColor="#16000000" zPosition="12" transparent="1">
			<convert type="ClockToText">Format:%e. %B</convert>
		</widget>
		<widget source="station0" render="Label" position="10,64"   size="208,24" font="Regular;18" halign="center" borderWidth="1" borderColor="#00192959" foregroundColor="#00ffffff" backgroundColor="#00203060,black,#00203060,horizontal" zPosition="1" />
		<widget source="station1" render="Label" position="220,64"  size="208,24" font="Regular;18" halign="center" borderWidth="1" borderColor="#00192959" foregroundColor="#00ffffff" backgroundColor="#00203060,black,#00203060,horizontal" zPosition="1" />
		<widget source="station2" render="Label" position="430,64"  size="208,24" font="Regular;18" halign="center" borderWidth="1" borderColor="#00192959" foregroundColor="#00ffffff" backgroundColor="#00203060,black,#00203060,horizontal" zPosition="1" />
		<widget source="station3" render="Label" position="640,64"  size="208,24" font="Regular;18" halign="center" borderWidth="1" borderColor="#00192959" foregroundColor="#00ffffff" backgroundColor="#00203060,black,#00203060,horizontal" zPosition="1" />
		<widget source="station4" render="Label" position="850,64"  size="208,24" font="Regular;18" halign="center" borderWidth="1" borderColor="#00192959" foregroundColor="#00ffffff" backgroundColor="#00203060,black,#00203060,horizontal" zPosition="1" />
		<widget source="station5" render="Label" position="1060,64" size="208,24" font="Regular;18" halign="center" borderWidth="1" borderColor="#00192959" foregroundColor="#00ffffff" backgroundColor="#00203060,black,#00203060,horizontal" zPosition="1" />
		<widget name="image0" position="10,90" size="134,76" alphatest="blend" scaleFlags="centerTop" zPosition="1" />
		<widget name="image1" position="220,90" size="134,76" alphatest="blend" scaleFlags="centerTop" zPosition="1" />
		<widget name="image2" position="430,90" size="134,76" alphatest="blend" scaleFlags="centerTop" zPosition="1" />
		<widget name="image3" position="640,90" size="134,76" alphatest="blend" scaleFlags="centerTop" zPosition="1" />
		<widget name="image4" position="850,90" size="134,76" alphatest="blend" scaleFlags="centerTop" zPosition="1" />
		<widget name="image5" position="1060,90" size="134,76" alphatest="blend" scaleFlags="centerTop" zPosition="1" />
		<widget name="play_button0" position="62,112" size="30,30" alphatest="blend" scaleFlags="centerScaled" zPosition="2" />
		<widget name="play_button1" position="272,112" size="30,30" alphatest="blend" scaleFlags="centerScaled" zPosition="2" />
		<widget name="play_button2" position="482,112" size="30,30" alphatest="blend" scaleFlags="centerScaled" zPosition="2" />
		<widget name="play_button3" position="692,112" size="30,30" alphatest="blend" scaleFlags="centerScaled" zPosition="2" />
		<widget name="play_button4" position="902,112" size="30,30" alphatest="blend" scaleFlags="centerScaled" zPosition="2" />
		<widget name="play_button5" position="1112,112" size="30,30" alphatest="blend" scaleFlags="centerScaled" zPosition="2" />
		<widget name="picon0" position="148,90" size="66,40" alphatest="blend" scaleFlags="centerTop" zPosition="1" />
		<widget name="picon1" position="358,90" size="66,40" alphatest="blend" scaleFlags="centerTop" zPosition="1" />
		<widget name="picon2" position="568,90" size="66,40" alphatest="blend" scaleFlags="centerTop" zPosition="1" />
		<widget name="picon3" position="778,90" size="66,40" alphatest="blend" scaleFlags="centerTop" zPosition="1" />
		<widget name="picon4" position="988,90" size="66,40" alphatest="blend" scaleFlags="centerTop" zPosition="1" />
		<widget name="picon5" position="1198,90" size="66,40" alphatest="blend" scaleFlags="centerTop" zPosition="1" />
		<widget source="pictime0" render="Label" position="148,130" size="66,24" foregroundColor="#00ffffff" backgroundColor="#00000000" font="Regular;20" valign="top" halign="center" zPosition="1" transparent="1" />
		<widget source="pictime1" render="Label" position="358,130" size="66,24" foregroundColor="#00ffffff" backgroundColor="#00000000" font="Regular;20" valign="top" halign="center" zPosition="1" transparent="1" />
		<widget source="pictime2" render="Label" position="568,130" size="66,24" foregroundColor="#00ffffff" backgroundColor="#00000000" font="Regular;20" valign="top" halign="center" zPosition="1" transparent="1" />
		<widget source="pictime3" render="Label" position="778,130" size="66,24" foregroundColor="#00ffffff" backgroundColor="#00000000" font="Regular;20" valign="top" halign="center" zPosition="1" transparent="1" />
		<widget source="pictime4" render="Label" position="988,130" size="66,24" foregroundColor="#00ffffff" backgroundColor="#00000000" font="Regular;20" valign="top" halign="center" zPosition="1" transparent="1" />
		<widget source="pictime5" render="Label" position="1198,130" size="66,24" foregroundColor="#00ffffff" backgroundColor="#00000000" font="Regular;20" valign="top" halign="center" zPosition="1" transparent="1" />
		<eLabel  name="line_pictext0" position="10,210"   size="208,2" backgroundColor="#0027153c,#101093,#101093,horizontal" zPosition="10" />
		<eLabel  name="line_pictext1" position="220,210"  size="208,2" backgroundColor="#0027153c,#101093,#101093,horizontal" zPosition="10" />
		<eLabel  name="line_pictext2" position="430,210"  size="208,2" backgroundColor="#0027153c,#101093,#101093,horizontal" zPosition="10" />
		<eLabel  name="line_pictext3" position="640,210"  size="208,2" backgroundColor="#0027153c,#101093,#101093,horizontal" zPosition="10" />
		<eLabel  name="line_pictext4" position="850,210"  size="208,2" backgroundColor="#0027153c,#101093,#101093,horizontal" zPosition="10" />
		<eLabel  name="line_pictext5" position="1060,210" size="208,2" backgroundColor="#0027153c,#101093,#101093,horizontal" zPosition="10" />
		<widget source="pictext0" render="Label" position="12,166"   size="206,44" font="Regular;18" valign="top" halign="left" zPosition="1" borderWidth="1" borderColor="#00192959" foregroundColor="#00ffffff" backgroundColor="#00203060,black,vertical" />
		<widget source="pictext1" render="Label" position="222,166"  size="206,44" font="Regular;18" valign="top" halign="left" zPosition="1" borderWidth="1" borderColor="#00192959" foregroundColor="#00ffffff" backgroundColor="#00203060,black,vertical" />
		<widget source="pictext2" render="Label" position="432,166"  size="206,44" font="Regular;18" valign="top" halign="left" zPosition="1" borderWidth="1" borderColor="#00192959" foregroundColor="#00ffffff" backgroundColor="#00203060,black,vertical" />
		<widget source="pictext3" render="Label" position="642,166"  size="206,44" font="Regular;18" valign="top" halign="left" zPosition="1" borderWidth="1" borderColor="#00192959" foregroundColor="#00ffffff" backgroundColor="#00203060,black,vertical" />
		<widget source="pictext4" render="Label" position="852,166"  size="206,44" font="Regular;18" valign="top" halign="left" zPosition="1" borderWidth="1" borderColor="#00192959" foregroundColor="#00ffffff" backgroundColor="#00203060,black,vertical" />
		<widget source="pictext5" render="Label" position="1062,166" size="210,44" font="Regular;18" valign="top" halign="left" zPosition="1" borderWidth="1" borderColor="#00192959" foregroundColor="#00ffffff" backgroundColor="#00203060,black,vertical" />
		<widget source="menulist0" render="Listbox" position="10,210" size="208,440" itemCornerRadiusSelected="10" itemGradientSelected="#101093,#203060,#0027153c,vertical" enableWrapAround="1" scrollbarMode="showNever" scrollbarBorderWidth="2" scrollbarForegroundColor="#00000000" scrollbarBorderColor="#7e7e7e">
			<convert type="TemplatedMultiContent">
				{"template": [  # 0=title, 1=single_asset_url, 3=endtime, 11=starttime_iso, 12=endtime_iso and all won't be used in skin
					MultiContentEntryText(pos=(0,6), size=(40,18), font=1, backcolor=13388098, color=16777215, backcolor_sel=13388098, color_sel=16777215, flags=RT_HALIGN_LEFT|RT_VALIGN_TOP, text=2),  # start time
					MultiContentEntryText(pos=(46,4), size=(144,100), font=0, flags=RT_HALIGN_LEFT|RT_VALIGN_TOP|RT_WRAP, text=4),  # description
					MultiContentEntryPixmapAlphaBlend(pos=(192,0), size=(14,14), png=5),  # timer_space
					MultiContentEntryPixmapAlphaBlend(pos=(0,24), size=(40,40), png=6),  # thumbspace
					MultiContentEntryPixmapAlphaBlend(pos=(6,32), size=(28,14), png=7),  # first iconspace
					MultiContentEntryPixmapAlphaBlend(pos=(6,48), size=(28,14), png=8),  # second iconspace
					MultiContentEntryPixmapAlphaBlend(pos=(6,64), size=(28,14), png=9),  # third iconspace
					MultiContentEntryPixmapAlphaBlend(pos=(6,80), size=(28,14), png=10),  # fourth iconspace
					MultiContentEntryPixmapAlphaBlend(pos=(6,96), size=(28,14), png=11),   # fifth iconspace
					MultiContentEntryProgress(pos=(46,0), size=(150,6), borderWidth=1, foreColor=0xcbcbcb, percent=-12)  # progressbar
					],
				"fonts": [gFont("Regular",18), gFont("Regular",16)],
				"itemHeight":110
				}
			</convert>
		</widget>
		<widget source="menulist1" render="Listbox" position="220,210" size="208,440" itemCornerRadiusSelected="10" itemGradientSelected="#101093,#203060,#0027153c,vertical" enableWrapAround="1" scrollbarMode="showNever" scrollbarBorderWidth="2" scrollbarForegroundColor="#00000000" scrollbarBorderColor="#7e7e7e">
			<convert type="TemplatedMultiContent">
				{"template": [  # 0=title, 1=single_asset_url, 3=endtime, 11=starttime_iso, 12=endtime_iso and all won't be used in skin
					MultiContentEntryText(pos=(0,6), size=(40,18), font=1, backcolor=13388098, color=16777215, backcolor_sel=13388098, color_sel=16777215, flags=RT_HALIGN_LEFT|RT_VALIGN_TOP, text=2),  # start time
					MultiContentEntryText(pos=(46,4), size=(144,100), font=0, flags=RT_HALIGN_LEFT|RT_VALIGN_TOP|RT_WRAP, text=4),  # description
					MultiContentEntryPixmapAlphaBlend(pos=(192,0), size=(14,14), png=5),  # timer_space
					MultiContentEntryPixmapAlphaBlend(pos=(0,24), size=(40,40), png=6),  # thumbspace
					MultiContentEntryPixmapAlphaBlend(pos=(6,32), size=(28,14), png=7),  # first iconspace
					MultiContentEntryPixmapAlphaBlend(pos=(6,48), size=(28,14), png=8),  # second iconspace
					MultiContentEntryPixmapAlphaBlend(pos=(6,64), size=(28,14), png=9),  # third iconspace
					MultiContentEntryPixmapAlphaBlend(pos=(6,80), size=(28,14), png=10),  # fourth iconspace
					MultiContentEntryPixmapAlphaBlend(pos=(6,96), size=(28,14), png=11),   # fifth iconspace
					MultiContentEntryProgress(pos=(46,0), size=(150,6), borderWidth=1, foreColor=0xcbcbcb, percent=-12)  # progressbar
					],
				"fonts": [gFont("Regular",18), gFont("Regular",16)],
				"itemHeight":110
				}
			</convert>
		</widget>
		<widget source="menulist2" render="Listbox" position="430,210" size="208,440" itemCornerRadiusSelected="10" itemGradientSelected="#101093,#203060,#0027153c,vertical" enableWrapAround="1" scrollbarMode="showNever" scrollbarBorderWidth="2" scrollbarForegroundColor="#00000000" scrollbarBorderColor="#7e7e7e">
			<convert type="TemplatedMultiContent">
				{"template": [  # 0=title, 1=single_asset_url, 3=endtime, 11=starttime_iso, 12=endtime_iso and all won't be used in skin
					MultiContentEntryText(pos=(0,6), size=(40,18), font=1, backcolor=13388098, color=16777215, backcolor_sel=13388098, color_sel=16777215, flags=RT_HALIGN_LEFT|RT_VALIGN_TOP, text=2),  # start time
					MultiContentEntryText(pos=(46,4), size=(144,100), font=0, flags=RT_HALIGN_LEFT|RT_VALIGN_TOP|RT_WRAP, text=4),  # description
					MultiContentEntryPixmapAlphaBlend(pos=(192,0), size=(14,14), png=5),  # timer_space
					MultiContentEntryPixmapAlphaBlend(pos=(0,24), size=(40,40), png=6),  # thumbspace
					MultiContentEntryPixmapAlphaBlend(pos=(6,32), size=(28,14), png=7),  # first iconspace
					MultiContentEntryPixmapAlphaBlend(pos=(6,48), size=(28,14), png=8),  # second iconspace
					MultiContentEntryPixmapAlphaBlend(pos=(6,64), size=(28,14), png=9),  # third iconspace
					MultiContentEntryPixmapAlphaBlend(pos=(6,80), size=(28,14), png=10),  # fourth iconspace
					MultiContentEntryPixmapAlphaBlend(pos=(6,96), size=(28,14), png=11),   # fifth iconspace
					MultiContentEntryProgress(pos=(46,0), size=(150,6), borderWidth=1, foreColor=0xcbcbcb, percent=-12)  # progressbar
					],
				"fonts": [gFont("Regular",18), gFont("Regular",16)],
				"itemHeight":110
				}
			</convert>
		</widget>
		<widget source="menulist3" render="Listbox" position="640,210" size="208,440" itemCornerRadiusSelected="10" itemGradientSelected="#101093,#203060,#0027153c,vertical" enableWrapAround="1" foregroundColorSelected="white" backgroundColor="#16000000" transparent="1" scrollbarMode="showNever" scrollbarBorderWidth="1" scrollbarWidth="10" scrollbarBorderColor="blue" scrollbarForegroundColor="#203060">
			<convert type="TemplatedMultiContent">
				{"template": [  # 0=title, 1=single_asset_url, 3=endtime, 11=starttime_iso, 12=endtime_iso and all won't be used in skin
					MultiContentEntryText(pos=(0,6), size=(40,18), font=1, backcolor=13388098, color=16777215, backcolor_sel=13388098, color_sel=16777215, flags=RT_HALIGN_LEFT|RT_VALIGN_TOP, text=2),  # start time
					MultiContentEntryText(pos=(46,4), size=(144,100), font=0, flags=RT_HALIGN_LEFT|RT_VALIGN_TOP|RT_WRAP, text=4),  # description
					MultiContentEntryPixmapAlphaBlend(pos=(192,0), size=(14,14), png=5),  # timer_space
					MultiContentEntryPixmapAlphaBlend(pos=(0,24), size=(40,40), png=6),  # thumbspace
					MultiContentEntryPixmapAlphaBlend(pos=(6,32), size=(28,14), png=7),  # first iconspace
					MultiContentEntryPixmapAlphaBlend(pos=(6,48), size=(28,14), png=8),  # second iconspace
					MultiContentEntryPixmapAlphaBlend(pos=(6,64), size=(28,14), png=9),  # third iconspace
					MultiContentEntryPixmapAlphaBlend(pos=(6,80), size=(28,14), png=10),  # fourth iconspace
					MultiContentEntryPixmapAlphaBlend(pos=(6,96), size=(28,14), png=11),   # fifth iconspace
					MultiContentEntryProgress(pos=(46,0), size=(150,6), borderWidth=1, foreColor=0xcbcbcb, percent=-12)  # progressbar
					],
				"fonts": [gFont("Regular",18), gFont("Regular",16)],
				"itemHeight":110
				}
			</convert>
		</widget>
		<widget source="menulist4" render="Listbox" position="850,210" size="208,440" itemCornerRadiusSelected="10" itemGradientSelected="#101093,#203060,#0027153c,vertical" enableWrapAround="1" scrollbarMode="showNever" scrollbarBorderWidth="2" scrollbarForegroundColor="#00000000" scrollbarBorderColor="#7e7e7e">
			<convert type="TemplatedMultiContent">
				{"template": [  # 0=title, 1=single_asset_url, 3=endtime, 11=starttime_iso, 12=endtime_iso and all won't be used in skin
					MultiContentEntryText(pos=(0,6), size=(40,18), font=1, backcolor=13388098, color=16777215, backcolor_sel=13388098, color_sel=16777215, flags=RT_HALIGN_LEFT|RT_VALIGN_TOP, text=2),  # start time
					MultiContentEntryText(pos=(46,4), size=(144,100), font=0, flags=RT_HALIGN_LEFT|RT_VALIGN_TOP|RT_WRAP, text=4),  # description
					MultiContentEntryPixmapAlphaBlend(pos=(192,0), size=(14,14), png=5),  # timer_space
					MultiContentEntryPixmapAlphaBlend(pos=(0,24), size=(40,40), png=6),  # thumbspace
					MultiContentEntryPixmapAlphaBlend(pos=(6,32), size=(28,14), png=7),  # first iconspace
					MultiContentEntryPixmapAlphaBlend(pos=(6,48), size=(28,14), png=8),  # second iconspace
					MultiContentEntryPixmapAlphaBlend(pos=(6,64), size=(28,14), png=9),  # third iconspace
					MultiContentEntryPixmapAlphaBlend(pos=(6,80), size=(28,14), png=10),  # fourth iconspace
					MultiContentEntryPixmapAlphaBlend(pos=(6,96), size=(28,14), png=11),   # fifth iconspace
					MultiContentEntryProgress(pos=(46,0), size=(150,6), borderWidth=1, foreColor=0xcbcbcb, percent=-12)  # progressbar
					],
				"fonts": [gFont("Regular",18), gFont("Regular",16)],
				"itemHeight":110
				}
			</convert>
		</widget>
		<widget source="menulist5" render="Listbox" position="1060,210" size="212,440" itemCornerRadiusSelected="10" itemGradientSelected="#101093,#203060,#0027153c,vertical" enableWrapAround="1" scrollbarMode="showNever" scrollbarBorderWidth="2" scrollbarForegroundColor="#00000000" scrollbarBorderColor="#7e7e7e">
			<convert type="TemplatedMultiContent">
				{"template": [  # 0=title, 1=single_asset_url, 3=endtime, 11=starttime_iso, 12=endtime_iso and all won't be used in skin
					MultiContentEntryText(pos=(0,6), size=(40,18), font=1, backcolor=13388098, color=16777215, backcolor_sel=13388098, color_sel=16777215, flags=RT_HALIGN_LEFT|RT_VALIGN_TOP, text=2),  # start time
					MultiContentEntryText(pos=(46,4), size=(144,100), font=0, flags=RT_HALIGN_LEFT|RT_VALIGN_TOP|RT_WRAP, text=4),  # description
					MultiContentEntryPixmapAlphaBlend(pos=(192,0), size=(14,14), png=5),  # timer_space
					MultiContentEntryPixmapAlphaBlend(pos=(0,24), size=(40,40), png=6),  # thumbspace
					MultiContentEntryPixmapAlphaBlend(pos=(6,32), size=(28,14), png=7),  # first iconspace
					MultiContentEntryPixmapAlphaBlend(pos=(6,48), size=(28,14), png=8),  # second iconspace
					MultiContentEntryPixmapAlphaBlend(pos=(6,64), size=(28,14), png=9),  # third iconspace
					MultiContentEntryPixmapAlphaBlend(pos=(6,80), size=(28,14), png=10),  # fourth iconspace
					MultiContentEntryPixmapAlphaBlend(pos=(6,96), size=(28,14), png=11),   # fifth iconspace
					MultiContentEntryProgress(pos=(46,0), size=(150,6), borderWidth=1, foreColor=0xcbcbcb, percent=-12)  # progressbar
					],
				"fonts": [gFont("Regular",18), gFont("Regular",16)],
				"itemHeight":110
				}
			</convert>
		</widget>
		<eLabel name="button_red" position="20,660" size="6,36" backgroundColor="#00821c17,#00fe0000,vertical" zPosition="1" />
		<eLabel name="button_green" position="160,660" size="6,36" backgroundColor="#00006600,#0024a424,vertical" zPosition="1" />
		<eLabel name="button_yellow" position="266,660" size="6,36" backgroundColor="#007a6213,#00e6c619,vertical" zPosition="1" />
		<eLabel name="button_blue" position="372,660" size="6,36" backgroundColor="#101093,#4040ff,vertical" zPosition="1" />
		<widget source="key_red" render="Label" position="30,666" size="130,26" font="Regular;18" valign="center" halign="left" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
		<widget source="key_green" render="Label" position="170,666" size="96,26" font="Regular;18" valign="center" halign="left" wrap="ellipsis" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
		<widget source="key_yellow" render="Label" position="276,666" size="96,26" font="Regular;18" valign="center" halign="left" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
		<widget source="key_blue" render="Label" position="382,666" size="96,26" font="Regular;18" valign="center" halign="left" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
		<eLabel text="Seite -" position="414,666" size="54,26" font="Regular;18" valign="center" halign="right" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
		<ePixmap position="474,666" size="46,28" pixmap="{tvs_path}/pics/{res}/icons/ch_plus_minus.png" alphatest="blend" zPosition="1" />
		<eLabel text="Seite +" position="526,666" size="54,26" font="Regular;18" valign="center" halign="left" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
		<ePixmap position="610,666" size="46,28" pixmap="{tvs_path}/pics/{res}/icons/ok.png" alphatest="blend" zPosition="1" />
		<eLabel text="Details" position="660,666" size="100,26" font="Regular;18" valign="center" halign="left" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
		<eLabel text="Tag -" position="708,666" size="76,26" font="Regular;18" valign="center" halign="right" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
		<ePixmap position="788,666" size="46,28" pixmap="{tvs_path}/pics/{res}/icons/left0right.png" alphatest="blend" zPosition="1" />
		<eLabel text="Tag +" position="840,666" size="76,26" font="Regular;18" valign="center" halign="left" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
		<ePixmap position="930,666" size="76,28" pixmap="{tvs_path}/pics/{res}/icons/info_epg.png" alphatest="blend" zPosition="1" />
		<widget source="key_info" render="Label" position="1010,666" size="120,26" font="Regular;18" valign="center" halign="left" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
		<widget name="playpic" position="1104,666" size="20,28" alphatest="blend" zPosition="2" />
		<widget source="key_play" render="Label" position="1126,666" size="160,26" font="Regular;18" valign="center" halign="left" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
	</screen>
	"""

	def __init__(self, session):
		self.session = session
		self.skin = self.skin.replace("{tvs_path}", tvglobals.PLUGINPATH).replace("{res}", tvglobals.RESOLUTION)
		Screen.__init__(self, session)
		self.refresh_active, self.refresh_stopped, self.prefetch_active = False, False, False
		self.refresh_lock = Lock()
		self.refresh_pending = False
		self.key_exit_pressed, self.load_all_epgstop = False, False
		self.curr_date_dt = datetime.now(tz=None).replace(hour=20, minute=15, second=0, microsecond=0)  # allways start with primetime '20:15'
		self.currday_delta, self.len_import_dict, self.total_assets_count = 0, 0, 0
		self.abs_col = 0  # absolute x-position in skinmatrix
		self.base_row, self.rel_row = 0, 0  # visible window start and cursor row on screen
		self.filtersettings = loads(config.plugins.tvspielfilm.filtersettings.value)
		self.window_start_dt = None
		self.show_running = False  # 'Jetzt im TV': start each column at the currently running program
		self.asset_title, self.curr_imdb_id, self.curr_tmdb_id = "", "", ""
		self.zap_allowed = False
		self.curr_service_ref = ""
		self.timestart_dt = datetime.now(tz=None)
		self.skinmatrix, self.len_assets = [], []
		self.jumpindex, self.databases = 0, 0
		self.jumplist = ("Jetzt im TV", "20:15 im TV", "22:00 im TV", "00:00 im TV")
		self["release"] = StaticText(self.RELEASE)
		for wname in ["reviewdate", "long_status", "progress_txt", "short_status", "key_info", "key_play"]:
			self[wname] = StaticText()
		for wname in ["prefetchpic", "playpic"]:
			self[wname] = Pixmap()
			self[wname].hide()
		for wname in ["progressbar", "prefetchbar"]:
			self[wname] = ProgressBar()
			self[wname].hide()
		for column in range(self.COLUMNS):
			self[f"station{column}"] = StaticText()
			self[f"picon{column}"] = Pixmap()
			self[f"image{column}"] = Pixmap()
			self[f"play_button{column}"] = Pixmap()
			self[f"pictime{column}"] = StaticText()
			self[f"pictext{column}"] = StaticText()
			self[f"station{column}"] = StaticText()
			self[f"menulist{column}"] = List()
			self[f"menulist{column}"].onSelectionChanged.append(lambda column=column: self.refresh_headline(column))
		self["key_red"] = StaticText(self.jumplist[self.jumpindex])
		self["key_green"] = StaticText("Timer")
		self["key_yellow"] = StaticText("EPG-Suche")
		self["key_blue"] = StaticText("")
		self["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "ButtonSetupActions"], {
				"ok": self.key_ok,
				"cancel": self.key_exit,
				"left": self.key_left,
				"right": self.key_right,
				"up": self.key_up,
				"down": self.key_down,
				"channelup": self.page_up,
				"channeldown": self.page_down,
				"previous": self.prev_day,
				"next": self.next_day,
				"info": self.key_info,
				"epg": self.key_info,
				"red": self.key_red,
				"red_long": self.key_redlong,
				"green": self.key_green,
				"blue": self.zapto_current,
				"yellow": self.open_epg_search,
				"play": self.play_trailer,
				"playpause": self.play_trailer
				}, -1)
		self.IMPORTDICT = self.read_imported_file()  # load imported channel data
		self.channels = [(channel[1][1], channel[0]) for channel in self.IMPORTDICT.items()]  # lade importierte Senderdaten (name, id)
		self.skinmatrix = [[] for _ in self.channels]
		self.len_assets = [0 for _ in self.channels]
		self.column_asset_urls = ["" for _ in range(self.COLUMNS)]
		self.trailer_assets = {}
		iconpath = self.ICONPATH
		self.icon_top = LoadPixmap(cached=True, path=join(iconpath, "top.png"))
		self.icon_tip = LoadPixmap(cached=True, path=join(iconpath, "tip.png"))
		self.icon_new = LoadPixmap(cached=True, path=join(iconpath, "new.png"))
		self.icon_live = LoadPixmap(cached=True, path=join(iconpath, "live.png"))
		self.icon_time = LoadPixmap(cached=True, path=join(iconpath, "timer.png"))
		self.init_asset_prefetch()
		self.onLayoutFinish.append(self.layout_finished)

	def layout_finished(self):
		iconpath = self.ICONPATH
		for widget, iconfile in [("playpic", "play.png"), ("prefetchpic", "prefetch.png")]:
			iconfile = join(iconpath, iconfile)
			if isfile(iconfile):
				self[widget].instance.setPixmapFromFile(iconfile)
			self[widget].hide()
		playbutton_file = join(iconpath, "playbutton.png")
		for column in range(self.COLUMNS):
			if isfile(playbutton_file):
				self[f"play_button{column}"].instance.setPixmapFromFile(playbutton_file)
			self[f"play_button{column}"].hide()
		for column in range(self.COLUMNS):
			self[f"menulist{column}"].enableAutoNavigation(False)  # keys are handled by this screen, not by the eListbox itself
		self.set_selection_column(0)
		callInThread(self.refresh_page)

	def refresh_page(self):  # threaded
		refresh_lock = getattr(self, "refresh_lock", None)
		if refresh_lock is None:
			return
		with refresh_lock:
			if getattr(self, "key_exit_pressed", True):
				return
			if self.refresh_active:
				self.refresh_pending = True
				return
			self.refresh_active = True
		while True:
			with self.refresh_lock:
				self.refresh_pending = False
			try:
				self._refresh_page()
			except Exception:
				with self.refresh_lock:
					self.refresh_pending = False
					self.refresh_active = False
				if self.key_exit_pressed:
					self.call_gui(self.close, False)
				raise
			with self.refresh_lock:
				if self.refresh_pending and not self.refresh_stopped and not self.key_exit_pressed:
					continue
				self.refresh_pending = False
				self.refresh_active = False
				break
		if self.key_exit_pressed:
			self.call_gui(self.close, False)

	def _refresh_page(self):
		def cleanup_cols():
			for column in range(columns):
				self.column_asset_urls[column] = ""
				self[f"station{column}"].setText("")
				self[f"picon{column}"].hide()
				self[f"image{column}"].hide()
				self[f"play_button{column}"].hide()
				self[f"pictime{column}"].setText("")
				self[f"pictext{column}"].setText("")
				widget = f"menulist{column}"
				self[widget].setList([])

		def update_column_list(column, widget, skin_list, selected_index):
			self[widget].updateList(skin_list)
			self[widget].setCurrentIndex(selected_index)
			current = self[widget].getCurrent()
			if current and len(current) > 14:
				self.request_headline(column, current[1])
			return current

		def update_selected_state():
			current = self[f"menulist{self.get_rel_col()}"].getCurrent()
			self.update_timer_state(current)
			self.update_zap_state(current)
			self.update_play_state(current)
			self["key_info"].setText(self.get_search_db_descs(self.databases))

		def create_skin_tuple():
			title = asset.get("title", "")  # won't be used in skin, needed for getCurrent()
			asset_url = asset.get("asset_url", "")  # won't be used in skin, needed for getCurrent()
			timestart_str = timestart_dt.strftime("%H:%M")
			timeend_str = timeend_dt.strftime("%H:%M")
			description = ", ".join(s for s in [title, asset.get('genre', ''), asset.get('country_year', '')] if s.strip())
			thumb_id_numeric = asset.get("thumb_id_numeric")
			offset, thumbpix = 0, None
			if thumb_id_numeric is not None:
				thumbfile = join(self.ICONPATH, f"thumb{thumb_id_numeric}.png")
				if isfile(thumbfile):
					offset, thumbpix = 2, self.call_gui(LoadPixmap, cached=True, path=thumbfile)  # move the following icons two spaces icons further down
			sref = import_dict.get(channel_id, [""])[0]
			has_timer = self.is_already_listed((timestart_dt.timestamp(), timeend_dt.timestamp()), sref, timer_list)
			timer_pic = self.icon_time if has_timer else None
			icons = [None, None, None, None, None]
			if asset.get("is_top"):
				icons[offset] = self.icon_top
				offset += 1
			elif asset.get("is_live"):
				icons[offset] = self.icon_live
				offset += 1
			if asset.get("is_tip"):
				icons[offset] = self.icon_tip
				offset += 1
			if asset.get("is_new"):
				icons[offset] = self.icon_new
			now_dt, durance = datetime.now(tz=None), timeend_dt - timestart_dt
			progress = int(((now_dt - timestart_dt) / durance) * 100) if durance else -1
			category = asset.get("category", "")
			return (title, asset_url, timestart_str, timeend_str, description, timer_pic, thumbpix, icons, progress, timestart_iso, timeend_iso, category)

		# start of 'refresh_page'
		rows, columns = self.ROWS, self.COLUMNS
		timer_list = set(self.get_timer_list())
		self.call_gui(cleanup_cols)
		page_start = self.get_base_col()
		local_channels = self.channels[page_start:page_start + columns]
		import_dict = self.IMPORTDICT
		self.len_import_dict = len(import_dict)
		self.update_widget("progressbar", "setRange", (0, columns))
		self.update_widget("progressbar", "setValue", 0)
		self.update_widget("long_status", "setText", "")
		dateonly_dt = self.curr_date_dt.date()
		now_dateonly_dt = datetime.now(tz=None).date()
		day_names = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
		weekday = "heute" if now_dateonly_dt == dateonly_dt else day_names[self.curr_date_dt.weekday()]
		span_starts_str = self.curr_date_dt.strftime("%H:%M")
		reviewdate = f"{weekday} {self.curr_date_dt.strftime('%d.%m.%Y')} | {self.currday_delta:+} Tag(e) | {span_starts_str} Uhr"
		self.update_widget("reviewdate", "setText", reviewdate)
		asset_counter, channel_counter = 0, 0
		span_ends_str = ""
		prefetch_list = []
		skinmatrix = [[] for _ in self.channels]
		for column in range(columns):
			if self.refresh_stopped:
				break
			if column < len(local_channels):  # fill up contents in used columns only
				name, channel_id = local_channels[column]
				absolute_column = page_start + column
				self.update_widget("progressbar", "setValue", column)
				self.update_widget("progress_txt", "setText", f"{column + 1}/{columns}")
				self.update_widget("short_status", "setText", f"Lade TVS-EPG Daten für {name}")
				self.update_widget(f"station{column}", "setText", name)
				widget = f"picon{column}"
				picon_file = self.get_picon_file(channel_id)
				if picon_file:
					self.call_gui(lambda: self[widget].instance.setPixmapFromFile(picon_file))
					self.update_widget(widget, "show")
				else:
					self.update_widget(widget, "hide")
				err_msg, assets = self.get_channel_assets(self.curr_date_dt, channel_id)
				for asset in assets:
					if self.refresh_stopped:
						break
					timestart_iso, timeend_iso = asset.get("time_start"), asset.get("time_end")
					if timestart_iso and timeend_iso:
						try:
							timestart_dt = datetime.fromisoformat(timestart_iso).replace(tzinfo=None)
							timeend_dt = datetime.fromisoformat(timeend_iso).replace(tzinfo=None)
						except ValueError:
							continue
						skinmatrix[absolute_column].append(create_skin_tuple())
				skinmatrix[absolute_column].sort(key=lambda entry: entry[9])
				self.len_assets[absolute_column] = len(skinmatrix[absolute_column])
				channel_counter += 1
		self.skinmatrix = skinmatrix
		if self.window_start_dt is None and 0 <= self.abs_col < len(self.skinmatrix):
			active_rows = self.skinmatrix[self.abs_col]
			first_row = self.get_row_from_start_dt(self.abs_col, self.curr_date_dt)
			if first_row < len(active_rows):
					self.window_start_dt = datetime.fromisoformat(active_rows[first_row][9]).replace(tzinfo=None)
			else:
				self.window_start_dt = self.curr_date_dt
		anchor_dt = self.window_start_dt or self.curr_date_dt
		visible_top_datetimes = []
		for column in range(columns):
			absolute_column = page_start + column
			if absolute_column >= len(self.skinmatrix):
				continue
			all_rows = self.skinmatrix[absolute_column]
			first_row = self.get_row_from_start_dt(absolute_column, anchor_dt)
			visible_rows = all_rows[first_row:first_row + rows]
			if visible_rows:
				try:
					visible_top_datetimes.append(datetime.fromisoformat(visible_rows[0][9]).replace(tzinfo=None))
				except (TypeError, ValueError):
					continue
			if absolute_column == self.abs_col:
				self.base_row = first_row
				if visible_rows:
					self.rel_row = min(max(self.rel_row, 0), len(visible_rows) - 1)
			skin_list = [entry[:6] + (entry[6],) + tuple(entry[7]) + entry[8:] for entry in visible_rows]
			leader_found = False
			for entry in visible_rows:
				asset_url = entry[1]
				if not asset_url:
					continue
				if not leader_found:
					leader_found = True
				else:
					prefetch_list.append(asset_url)
			if visible_rows:
				span_ends_str = visible_rows[-1][2]
			asset_counter += len(visible_rows)
			widget = f"menulist{column}"
			selected_index = min(self.rel_row, len(skin_list) - 1) if skin_list else 0
			self.call_gui(update_column_list, column, widget, skin_list, selected_index)
		self.call_gui(update_selected_state)
		header_date = self.curr_date_dt.date()
		header_day_delta = self.currday_delta
		header_start_str = span_starts_str
		if len(local_channels) == columns and len(visible_top_datetimes) == columns and all(start_dt.date() > header_date for start_dt in visible_top_datetimes):
			header_date = visible_top_datetimes[0].date()
			header_day_delta += (header_date - self.curr_date_dt.date()).days
			header_start_str = visible_top_datetimes[0].strftime("%H:%M")
		header_weekday = "heute" if now_dateonly_dt == header_date else day_names[header_date.weekday()]
		reviewdate = f"{header_weekday} {header_date.strftime('%d.%m.%Y')} | {header_day_delta:+} Tag(e) | {header_start_str} - {span_ends_str} Uhr"
		self.update_widget("reviewdate", "setText", reviewdate)
		self.update_widget("progressbar", "setValue", 0)
		self.update_widget("progress_txt", "setText", "")
		self.update_widget("short_status", "setText", "")
		self.update_widget("long_status", "setText", f"{asset_counter} Einträge in insgesamt {channel_counter} Sendern gefunden.")
		self.start_asset_prefetch(prefetch_list)
		if self.refresh_stopped:
			self.call_gui(self.key_exit)

	def get_row_from_start_dt(self, column, start_dt):
		if not 0 <= column < len(self.skinmatrix):
			return 0
		if self.show_running:
			for row, entry in enumerate(self.skinmatrix[column]):
				if datetime.fromisoformat(entry[9]).replace(tzinfo=None) <= start_dt < datetime.fromisoformat(entry[10]).replace(tzinfo=None):
					return row
		for row, entry in enumerate(self.skinmatrix[column]):
			if datetime.fromisoformat(entry[9]).replace(tzinfo=None) >= start_dt:
				return row
		return len(self.skinmatrix[column])

	def align_active_column(self):
		if self.window_start_dt is not None and 0 <= self.abs_col < len(self.skinmatrix):
			rows = self.skinmatrix[self.abs_col]
			self.base_row = self.get_row_from_start_dt(self.abs_col, self.window_start_dt)
			visible_count = min(self.ROWS, len(rows) - self.base_row)
			self.rel_row = min(self.rel_row, visible_count - 1) if visible_count else 0
			if visible_count:
				self[f"menulist{self.get_rel_col()}"].setCurrentIndex(self.rel_row)

	def refresh_headline(self, column):
		current = self[f"menulist{column}"].getCurrent()
		if column == self.get_rel_col():
			self.update_timer_state(current)
			self.update_zap_state(current)
			self.update_play_state(current)
			self["key_info"].setText(self.get_search_db_descs(self.databases))
		if not self.refresh_active:
			if current and len(current) > 14:
				self.request_headline(column, current[1])

	def update_timer_state(self, current):
		has_timer = False
		if current and len(current) > 14 and 0 <= self.abs_col < len(self.channels):
			try:
				start_ts = datetime.fromisoformat(current[13]).replace(tzinfo=None).timestamp()
				end_ts = datetime.fromisoformat(current[14]).replace(tzinfo=None).timestamp()
			except (TypeError, ValueError):
				pass
			else:
				channel_id = self.channels[self.abs_col][1]
				sref = self.IMPORTDICT.get(channel_id, [""])[0]
				has_timer = self.is_already_listed((start_ts, end_ts), sref)
		self["key_green"].setText("" if has_timer else "Timer")

	def update_zap_state(self, current):
		self.zap_allowed = False
		self.curr_service_ref = ""
		if current and len(current) > 14 and 0 <= self.abs_col < len(self.channels):
			try:
				timestart_dt = datetime.fromisoformat(current[13]).replace(tzinfo=None)
				timeend_dt = datetime.fromisoformat(current[14]).replace(tzinfo=None)
			except (TypeError, ValueError):
				pass
			else:
				channel_id = self.channels[self.abs_col][1]
				self.curr_service_ref = self.IMPORTDICT.get(channel_id, ["", ""])[0]
				self.zap_allowed = bool(self.curr_service_ref) and timestart_dt <= datetime.now(tz=None) < timeend_dt
		self["key_blue"].setText("Zap" if self.zap_allowed else "")

	def update_play_state(self, current):
		if current and len(current) > 14:
			if self.trailer_assets.get(current[1], ""):  # current[1] = asset_url
				self.asset_title = current[0]
				self["playpic"].show()
				self["key_play"].setText("Trailer abspielen")
				return
		self["playpic"].hide()
		self["key_play"].setText("")

	def play_trailer(self):
		current = self[f"menulist{self.get_rel_col()}"].getCurrent()
		if current and len(current) > 14:
			trailer_data = self.trailer_assets.get(current[1], "")
			if trailer_data and "," in trailer_data:
				cid, lic = trailer_data.split(",")
				if cid and lic:
					self.asset_title = current[0]
					deferToThread(tvspassets.resolve_trailer_url, cid, lic).addCallback(self.play_trailer_start)

	def request_headline(self, column, asset_url):
		if 0 <= column < len(self.column_asset_urls) and self.column_asset_urls[column] != asset_url:
			self.column_asset_urls[column] = asset_url
			self[f"image{column}"].hide()
			callInThread(self.set_headline, column, asset_url)

	def set_headline(self, column, asset_url):
		err_msg, asset = self.get_single_asset(asset_url)  # get single asset for necessary details
		callFromThread(self.set_headline_ui, column, asset_url, err_msg, asset)

	def set_headline_ui(self, column, asset_url, err_msg, asset):
		if not 0 <= column < len(self.column_asset_urls) or self.column_asset_urls[column] != asset_url:
			return
		self.trailer_assets[asset_url] = asset.get("trailer_data", "")
		if column == self.get_rel_col():
			current = self[f"menulist{column}"].getCurrent()
			if current and len(current) > 14 and current[1] == asset_url:
				self.update_play_state(current)
		self[f"pictext{column}"].setText(asset.get("title", "{unbekannt}"))
		timestart_str = asset.get("time_start", "")
		self[f"pictime{column}"].setText(timestart_str[11:16] if timestart_str else "??:??")
		if img_url := asset.get("img_url", ""):
			img_file = self.image_filename(img_url)
			if isfile(img_file):
				self.set_col_image(img_file, asset_url=asset_url, column=column)
			else:  # download, save & set image
				callInThread(self.image_download, img_url, callback=self.set_col_image, asset_url=asset_url, column=column)
		if asset.get("trailer_data"):
			self[f"play_button{column}"].show()
		else:
			self[f"play_button{column}"].hide()

	def set_col_image(self, img_file, asset_url="", column=0):
		if 0 <= column < len(self.column_asset_urls) and self.column_asset_urls[column] == asset_url:
			widget = f"image{column}"
			if isfile(img_file):
				self[widget].instance.setPixmapFromFile(img_file)
				self[widget].show()
			else:
				self[widget].hide()

	def set_selection_column(self, column):  # initial state: only one column shows a cursor
		for index in range(self.COLUMNS):
			self[f"menulist{index}"].selectionEnabled(1 if index == column else 0)

	def change_col(self, old_col, new_col):
		if old_col != new_col:
			self[f"menulist{old_col}"].selectionEnabled(0)
			self[f"menulist{new_col}"].selectionEnabled(1)

	def get_base_col(self):  # set offset index for current column (no special shifting required)
		return (self.abs_col // self.COLUMNS) * self.COLUMNS

	def get_rel_col(self):  # get relative index for current column (no special shifting required)
		return self.abs_col % self.COLUMNS

	def get_page_number(self):  # get page number
		return self.abs_col // self.COLUMNS

	def refresh_page_acc_current_row(self):
		if 0 <= self.abs_col < len(self.skinmatrix):
			active_rows = self.skinmatrix[self.abs_col]
			if 0 <= self.base_row < len(active_rows):
				self.show_running = False
				try:
					self.window_start_dt = datetime.fromisoformat(active_rows[self.base_row][9]).replace(tzinfo=None)
				except (TypeError, ValueError):
					self.window_start_dt = self.curr_date_dt
				callInThread(self.refresh_page)

	def key_ok(self):
		current = self[f"menulist{self.get_rel_col()}"].getCurrent()
		if current and len(current) > 14:
			try:
				timestart_dt = datetime.fromisoformat(current[13]).replace(tzinfo=None)
				timeend_dt = datetime.fromisoformat(current[14]).replace(tzinfo=None)
			except (TypeError, ValueError):
				return
			now_dt = datetime.now(tz=None)
			zap_allowed = now_dt >= timestart_dt and now_dt < timeend_dt
			self.session.openWithCallback(self.key_ok_return, TVFullscreen, current[1], self.curr_date_dt, prefetch_owner=self, zap_allowed=zap_allowed)

	def key_ok_return(self, answer):
		if answer:
			self.close(True)

	def key_left(self):
		if self.channels:
			old_page = self.get_page_number()
			old_rel_col = self.get_rel_col()
			self.abs_col = (self.abs_col - 1) % len(self.channels)
			new_rel_col = self.get_rel_col()
			if self.get_page_number() != old_page:
				self.update_zap_state(None)
				self.update_play_state(None)
				self.change_col(old_rel_col, new_rel_col)
				callInThread(self.refresh_page)
			else:
				self.align_active_column()  # set cursor row before the selection becomes visible
				self.change_col(old_rel_col, new_rel_col)
				current = self[f"menulist{new_rel_col}"].getCurrent()
				self.update_timer_state(current)
				self.update_zap_state(current)
				self.update_play_state(current)
			self["key_info"].setText(self.get_search_db_descs(self.databases))

	def key_right(self):
		if self.channels:
			old_page = self.get_page_number()
			old_rel_col = self.get_rel_col()
			self.abs_col = (self.abs_col + 1) % len(self.channels)
			new_rel_col = self.get_rel_col()
			if self.get_page_number() != old_page:
				self.update_zap_state(None)
				self.update_play_state(None)
				self.change_col(old_rel_col, new_rel_col)
				callInThread(self.refresh_page)
			else:
				self.align_active_column()  # set cursor row before the selection becomes visible
				self.change_col(old_rel_col, new_rel_col)
				current = self[f"menulist{new_rel_col}"].getCurrent()
				self.update_timer_state(current)
				self.update_zap_state(current)
				self.update_play_state(current)
			self["key_info"].setText(self.get_search_db_descs(self.databases))

	def key_up(self):
		if 0 <= self.abs_col < len(self.skinmatrix):
			widget = f"menulist{self.get_rel_col()}"
			curr_index = self[widget].getCurrentIndex()
			active_rows = self.skinmatrix[self.abs_col]
			if not active_rows:
				return
			current_row = min(self.base_row + curr_index, len(active_rows) - 1)
			if curr_index > 0:
				current_row -= 1
				self.rel_row = curr_index - 1
				self[widget].up()
			elif current_row > 0:
				target_row = current_row - 1
				self.base_row = max(0, target_row - (self.ROWS - 1))
				self.rel_row = target_row - self.base_row
				self.refresh_page_acc_current_row()
			elif len(active_rows) > 1:
				self.base_row = max(0, len(active_rows) - self.ROWS)
				self.rel_row = len(active_rows) - self.base_row - 1
				self.refresh_page_acc_current_row()

	def key_down(self):
		if 0 <= self.abs_col < len(self.skinmatrix):
			widget = f"menulist{self.get_rel_col()}"
			curr_index = self[widget].getCurrentIndex()
			active_rows = self.skinmatrix[self.abs_col]
			if not active_rows:
				return
			current_row = min(self.base_row + curr_index, len(active_rows) - 1)
			target_row = current_row + 1
			visible_count = min(self.ROWS, len(active_rows) - self.base_row)
			if curr_index < visible_count - 1:
				self.rel_row = curr_index + 1
				self[widget].down()
			elif target_row < len(active_rows):
				self.base_row = target_row
				self.rel_row = 0
				self.refresh_page_acc_current_row()
			elif len(active_rows) > 1:
				self.base_row = 0
				self.rel_row = 0
				self.refresh_page_acc_current_row()

	def page_up(self):
		if 0 <= self.abs_col < len(self.skinmatrix):
			active_rows = self.skinmatrix[self.abs_col]
			if active_rows:
				curr_index = self[f"menulist{self.get_rel_col()}"].getCurrentIndex()
				current_row = min(self.base_row + curr_index, len(active_rows) - 1)
				if len(active_rows) > self.ROWS and self.base_row == 0:
					self.base_row = len(active_rows) - self.ROWS
					self.rel_row = self.ROWS - 1
					self.refresh_page_acc_current_row()
				else:
					target_row = max(0, current_row - (self.ROWS - 1))
					if target_row == current_row:
						return
					self.base_row = max(0, target_row - self.rel_row)
					self.rel_row = target_row - self.base_row
					self.refresh_page_acc_current_row()

	def page_down(self):
		if 0 <= self.abs_col < len(self.skinmatrix):
			active_rows = self.skinmatrix[self.abs_col]
			if active_rows:
				curr_index = self[f"menulist{self.get_rel_col()}"].getCurrentIndex()
				current_row = min(self.base_row + curr_index, len(active_rows) - 1)
				visible_count = min(self.ROWS, len(active_rows) - self.base_row)
				at_last_window = self.base_row + visible_count >= len(active_rows)
				if len(active_rows) > self.ROWS and at_last_window:
					self.base_row = 0
					self.rel_row = 0
					self.refresh_page_acc_current_row()
				else:
					target_row = min(len(active_rows) - 1, current_row + (self.ROWS - 1))
					if target_row == current_row:
						return
					self.base_row = max(0, target_row - self.rel_row)
					self.rel_row = target_row - self.base_row
					self.refresh_page_acc_current_row()

	def prev_day(self):
		self.change_day(-1)

	def next_day(self):
		self.change_day(1)

	def change_day(self, jump_days):
		desired_day = self.currday_delta + jump_days
		if -7 <= desired_day <= 13:
			self.currday_delta = desired_day
			self.curr_date_dt += timedelta(days=jump_days)
			if self.window_start_dt:
				self.window_start_dt += timedelta(days=jump_days)
			callInThread(self.refresh_page)

	def key_red(self):
		self.key_red_finish()

	def key_redlong(self):
		msgtext = "Wähle einen Zeitraum:"
		jumplist = [(jumptext, index) for index, jumptext in enumerate(self.jumplist)]
		self.session.openWithCallback(self.key_redlong_cb, ChoiceBox, list=jumplist, keys=[], title=msgtext)

	def key_redlong_cb(self, answer):
		if answer:
			self.jumpindex = self.jumplist.index(answer[0])
			self.key_red_finish()

	def key_red_finish(self):
		if self.jumpindex:
			hour, minute = map(int, self.jumplist[self.jumpindex][:5].split(":"))
			self.curr_date_dt = self.curr_date_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
		else:
			now_dt = datetime.now()
			self.curr_date_dt = self.curr_date_dt.replace(hour=now_dt.hour, minute=now_dt.minute, second=0, microsecond=0)
		self.window_start_dt = self.curr_date_dt
		self.show_running = not self.jumpindex
		callInThread(self.refresh_page)
		self.jumpindex = (self.jumpindex + 1) % len(self.jumplist)
		self["key_red"].setText(self.jumplist[self.jumpindex])

	def key_green(self):
		current = self[f"menulist{self.get_rel_col()}"].getCurrent()
		if current and len(current) > 14 and 0 <= self.abs_col < len(self.channels):
			title, shortdesc = current[0], current[4]
			channel_id = self.channels[self.abs_col][1]
			sref = self.IMPORTDICT.get(channel_id, ["", ""])[0]
			if sref:
				try:
					start_ts = datetime.fromisoformat(current[13]).replace(tzinfo=None).timestamp()
					end_ts = datetime.fromisoformat(current[14]).replace(tzinfo=None).timestamp()
				except (TypeError, ValueError):
					return
				self.build_record_timer(start_ts, end_ts, sref, title, shortdesc, self.finishkey_green)

	def finishkey_green(self, answer):
		if answer and not isinstance(answer, bool) and answer[0]:  # special case for close recursive
			self.session.nav.RecordTimer.record(answer[1])
			callInThread(self._refresh_page)

	def key_exit(self):
		with self.refresh_lock:
			self.key_exit_pressed = True
			refresh_active = self.refresh_active
			if refresh_active:
				self.refresh_stopped = True
				self.load_all_epgstop = True
		if refresh_active:
			return
		if self.prefetch_active:
			self.abort_asset_prefetch()
		else:
			self.close(False)  # return to main menu


class TVFullscreen(TVScreenHelper, Screen):
	skin = """
	<screen name="TVFullscreen" position="10,10" size="1260,700" resolution="1280,720" flags="wfNoBorder" backgroundColor="#16000000" transparent="0" title="TV Spielfilm Detailansicht">
		<eLabel position="780,476" size="456,2" backgroundColor="#0027153c,#00101093,black,horizontal" zPosition="10" />
		<eLabel position="10,188" size="752,2" backgroundColor="#0027153c,#00101093,black,horizontal" zPosition="10" />
		<eLabel position="10,600" size="752,2" backgroundColor="#0027153c,#00101093,black,horizontal" zPosition="10" />
		<eLabel name="Gradient_BlueBlack" position="0,64" size="1260,590" zPosition="-10" backgroundColor="#10060613" />
		<eLabel position="0,0" size="1260,60" backgroundColor=" black,#00203060,horizontal" zPosition="1" />
		<eLabel position="0,60" size="1260,2" backgroundColor="#0027153c,#101093,black,horizontal" zPosition="10" />
		<eLabel position="0,652" size="1260,2" backgroundColor="#0027153c,#101093,black,horizontal" zPosition="10" />
		<ePixmap position="0,0" size="220,60" pixmap="{tvs_path}/pics/{res}/logos/TVSpielfilm.png" alphatest="blend" zPosition="13" />
		<widget source="release" render="Label" position="180,28" size="80,20" font="Regular;18" textBorderColor="#00505050" textBorderWidth="1" foregroundColor="#00ffff00" backgroundColor="#16000000" valign="center" zPosition="12" transparent="1" />
		<widget source="reviewdate" render="Label" position="270,34" size="720,24" font="Regular;18" foregroundColor="white" backgroundColor="#16000000" halign="center" valign="center" zPosition="12" transparent="1" />
		<widget source="global.CurrentTime" render="Label" position="1110,0" size="140,60" font="Regular;46" noWrap="1" halign="center" valign="bottom" foregroundColor="white" backgroundColor="#16000000" zPosition="12" transparent="1">
		<convert type="ClockToText">Default</convert>
			</widget>
		<widget source="global.CurrentTime" render="Label" position="1000,2" size="100,26" font="Regular;16" noWrap="1" halign="right" valign="bottom" foregroundColor="white" backgroundColor="#16000000" zPosition="12" transparent="1">
		<convert type="ClockToText">Format:%A</convert>
			</widget>
		<widget source="global.CurrentTime" render="Label" position="1000,26" size="100,26" font="Regular;16" noWrap="1" halign="right" valign="bottom" foregroundColor="white" backgroundColor="#16000000" zPosition="12" transparent="1">
			<convert type="ClockToText">Format:%e. %B</convert>
		</widget>
		<widget name="thumb" position="38,96" size="60,60" alphatest="blend" />
		<widget source="timestartend" render="Label" position="90,66" size="490,30" font="Regular;20" backgroundColor="#16000000" transparent="1" />
		<widget source="editorial" render="Label" position="90,88" size="490,26" font="Regular;18" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
		<widget source="conclusion" render="Label" position="90,106" size="490,54" font="Regular;20" foregroundColor="#0092cbdf" backgroundColor="#16000000" transparent="1" />
		<widget name="picon" position="586,66" size="148,88" alphatest="blend" scaleFlags="scale" zPosition="1" />
		<widget source="channel_name" render="Label" position="548,158" size="220,32" font="Regular;24" halign="center" foregroundColor="#0092cbdf" backgroundColor="#16000000" transparent="1" />
		<widget name="has_timer" position="718,68" size="14,14" alphatest="blend" zPosition="1" />
		<widget source="title" render="Label" position="center,0" size="720,36" font="Regular;24" foregroundColor="#0092cbdf" backgroundColor="#16000000" transparent="1" wrap="ellipsis" halign="center" valign="center" zPosition="10" />
		<widget source="repeat_hint" render="Label" position="10,600" size="750,46" font="Regular;18" valign="center" halign="left" backgroundColor="#16000000" transparent="1" />
		<widget name="image" position="794,70" size="400,300" alphatest="blend" scaleFlags="centerBottom" zPosition="1" />
		<widget name="playbutton" position="970,194" size="60,60" alphatest="blend" zPosition="2" />
		<widget name="fsk" position="800,326" size="40,40" alphatest="blend" zPosition="2" />
		<widget source="credits" render="Label" position="756,372" size="474,22" font="Regular;16" foregroundColor="grey" backgroundColor="#16000000" halign="center" transparent="1" />
		<widget name="is_top" position="94,164" size="28,14" alphatest="blend" zPosition="1" />
		<widget name="is_live" position="94,164" size="28,14" alphatest="blend" zPosition="1" />
		<widget name="is_tip" position="134,164" size="28,14" alphatest="blend" zPosition="1" />
		<widget name="is_new" position="174,164" size="28,14" alphatest="blend" zPosition="1" />
		<widget name="is_tmdb" position="214,164" size="28,14" alphatest="blend" zPosition="1" />
		<widget source="ratinglabel0l" render="Label" position="756,396" size="90,24" font="Regular;16" halign="center" foregroundColor="#10333333" backgroundColor="#16000000" transparent="1" />
		<widget source="ratinglabel0h" render="Label" position="756,396" size="90,24" font="Regular;16" halign="center" foregroundColor="white" backgroundColor="#16000000" transparent="1" />
		<widget name="ratingdots0" position="778,420" size="46,16" alphatest="blend" />
		<widget source="ratinglabel1l" render="Label" position="852,396" size="90,24" font="Regular;16" halign="center" foregroundColor="#10333333" backgroundColor="#16000000" transparent="1" />
		<widget source="ratinglabel1h" render="Label" position="852,396" size="90,24" font="Regular;16" halign="center" foregroundColor="white" backgroundColor="#16000000" transparent="1" />
		<widget name="ratingdots1" position="874,420" size="46,16" alphatest="blend" />
		<widget source="ratinglabel2l" render="Label" position="948,396" size="90,24" font="Regular;16" halign="center" foregroundColor="#10333333" backgroundColor="#16000000" transparent="1" />
		<widget source="ratinglabel2h" render="Label" position="948,396" size="90,24" font="Regular;16" halign="center" foregroundColor="white" backgroundColor="#16000000" transparent="1" />
		<widget name="ratingdots2" position="970,420" size="46,16" alphatest="blend" />
		<widget source="ratinglabel3l" render="Label" position="1044,396" size="90,24" font="Regular;16" halign="center" foregroundColor="#10333333" backgroundColor="#16000000" transparent="1" />
		<widget source="ratinglabel3h" render="Label" position="1044,396" size="90,24" font="Regular;16" halign="center" foregroundColor="white" backgroundColor="#16000000" transparent="1" />
		<widget name="ratingdots3" position="1066,420" size="46,16" alphatest="blend" />
		<widget source="ratinglabel4l" render="Label" position="1140,396" size="90,24" font="Regular;16" halign="center" foregroundColor="#10333333" backgroundColor="#16000000" transparent="1" />
		<widget source="ratinglabel4h" render="Label" position="1140,396" size="90,24" font="Regular;16" halign="center" foregroundColor="white" backgroundColor="#16000000" transparent="1" />
		<widget name="ratingdots4" position="1162,420" size="46,16" alphatest="blend" />
		<widget source="imdb_rating" render="Label" position="292,160" size="170,22" font="Regular;16" halign="center" foregroundColor="yellow" backgroundColor="#16000000" transparent="1" zPosition="1" />
		<widget name="long_description" position="10,190" size="750,410" font="Regular;20" backgroundColor="#16000000" transparent="1" scrollbarMode="showOnDemand" scrollbarBorderWidth="1" scrollbarWidth="10" scrollbarBorderColor="blue" scrollbarForegroundColor="#203060" />
		<widget source="title" render="RunningText" options="movetype=running,startpoint=0,startdelay=2000,wrap=0,always=0,repeat=2,oneshot=1" position="780,444" size="456,34" font="Regular;22" halign="left" foregroundColor="#92cbdf" backgroundColor="#16000000" transparent="1" />
		<widget source="type_label0l" render="Label" position="780,488" size="140,26" font="Regular;18" foregroundColor="#10333333" backgroundColor="#16000000" transparent="1" />
		<widget source="type_label0h" render="Label" position="780,488" size="140,26" font="Regular;18" foregroundColor="white" backgroundColor="#16000000" transparent="1" />
		<widget source="type_label1l" render="Label" position="780,514" size="140,26" font="Regular;18" foregroundColor="#10333333" backgroundColor="#16000000" transparent="1" />
		<widget source="type_label1h" render="Label" position="780,514" size="140,26" font="Regular;18" foregroundColor="white" backgroundColor="#16000000" transparent="1" />
		<widget source="type_label2l" render="Label" position="780,540" size="140,26" font="Regular;18" foregroundColor="#10333333" backgroundColor="#16000000" transparent="1" />
		<widget source="type_label2h" render="Label" position="780,540" size="140,26" font="Regular;18" foregroundColor="white" backgroundColor="#16000000" transparent="1" />
		<widget source="type_label3l" render="Label" position="780,566" size="140,26" font="Regular;18" foregroundColor="#10333333" backgroundColor="#16000000" transparent="1" />
		<widget source="type_label3h" render="Label" position="780,566" size="140,26" font="Regular;18" foregroundColor="white" backgroundColor="#16000000" transparent="1" />
		<widget source="type_label4l" render="Label" position="780,592" size="140,26" font="Regular;18" foregroundColor="#10333333" backgroundColor="#16000000" transparent="1" />
		<widget source="type_label4h" render="Label" position="780,592" size="140,26" font="Regular;18" foregroundColor="white" backgroundColor="#16000000" transparent="1" />
		<widget source="type_label5l" render="Label" position="780,618" size="140,26" font="Regular;18" foregroundColor="#10333333" backgroundColor="#16000000" transparent="1" />
		<widget source="type_label5h" render="Label" position="780,618" size="140,26" font="Regular;18" foregroundColor="white" backgroundColor="#16000000" transparent="1" />
		<widget source="type_text0" render="Label" position="930,488" size="320,26" font="Regular;18" backgroundColor="#16000000" transparent="1" />
		<widget source="type_text1" render="Label" position="930,514" size="320,26" font="Regular;18" backgroundColor="#16000000" transparent="1" />
		<widget source="type_text2" render="Label" position="930,540" size="320,26" font="Regular;18" backgroundColor="#16000000" transparent="1" />
		<widget source="type_text3" render="Label" position="930,566" size="320,26" font="Regular;18" backgroundColor="#16000000" transparent="1" />
		<widget source="type_text4" render="Label" position="930,592" size="320,26" font="Regular;18" backgroundColor="#16000000" transparent="1" />
		<widget source="type_text5" render="Label" position="930,618" size="320,26" font="Regular;18" backgroundColor="#16000000" transparent="1" />
		<eLabel name="button_red" position="10,660" size="6,36" backgroundColor="#00821c17,#00fe0000,vertical" zPosition="1" />
		<eLabel name="button_green" position="150,660" size="6,36" backgroundColor="#00006600,#0024a424,vertical" zPosition="1" />
		<eLabel name="button_yellow" position="256,660" size="6,36" backgroundColor="#007a6213,#00e6c619,vertical" zPosition="1" />
		<eLabel name="button_blue" position="362,660" size="6,36" backgroundColor="#101093,#4040ff,vertical" zPosition="1" />
		<widget source="key_red" render="Label" position="20,666" size="130,26" font="Regular;18" valign="center" halign="left" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
		<widget source="key_green" render="Label" position="160,666" size="96,26" font="Regular;18" valign="center" halign="left" wrap="ellipsis" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
		<widget source="key_yellow" render="Label" position="266,666" size="96,26" font="Regular;18" valign="center" halign="left" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
		<widget source="key_blue" render="Label" position="372,666" size="96,26" font="Regular;18" valign="center" halign="left" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
		<ePixmap position="600,666" size="46,28" pixmap="{tvs_path}/pics/{res}/icons/ok.png" alphatest="blend" zPosition="1" />
		<eLabel text="Zurück" position="650,666" size="100,26" font="Regular;18" valign="center" halign="left" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
		<ePixmap position="920,666" size="76,28" pixmap="{tvs_path}/pics/{res}/icons/info_epg.png" alphatest="blend" zPosition="1" />
		<widget source="key_info" render="Label" position="1000,666" size="120,26" font="Regular;18" valign="center" halign="left" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
		<widget name="playpic" position="1094,666" size="20,28" alphatest="blend" zPosition="2" />
		<widget source="key_play" render="Label" position="1116,666" size="160,26" font="Regular;18" valign="center" halign="left" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
		<eLabel position="88,158" size="376,26" zPosition="-1" backgroundColor="#00505050" cornerRadius="2" />
		<eLabel position="90,160" size="372,22" zPosition="0" backgroundColor="#16000000" cornerRadius="2" />
		<eLabel position="92,162" size="32,18" zPosition="-1" backgroundColor="#00505050" cornerRadius="2" />
		<eLabel position="93,163" size="30,16" zPosition="0" backgroundColor="#16000000" cornerRadius="2" />
		<eLabel position="128,162" size="1,18" zPosition="1" backgroundColor="#00505050" />
		<eLabel position="132,162" size="32,18" zPosition="-1" backgroundColor="#00505050" cornerRadius="2" />
		<eLabel position="133,163" size="30,16" zPosition="0" backgroundColor="#16000000" cornerRadius="2" />
		<eLabel position="168,162" size="1,18" zPosition="1" backgroundColor="#00505050" />
		<eLabel position="172,162" size="32,18" zPosition="-1" backgroundColor="#00505050" cornerRadius="2" />
		<eLabel position="173,163" size="30,16" zPosition="0" backgroundColor="#16000000" cornerRadius="2" />
		<eLabel position="208,162" size="1,18" zPosition="1" backgroundColor="#00505050" />
		<eLabel position="212,162" size="32,18" zPosition="-1" backgroundColor="#00505050" cornerRadius="2" />
		<eLabel position="213,163" size="30,16" zPosition="0" backgroundColor="#16000000" cornerRadius="2" />
		<eLabel position="248,162" size="1,18" zPosition="1" backgroundColor="#00505050" />
		<eLabel position="252,162" size="32,18" zPosition="-1" backgroundColor="#00505050" cornerRadius="2" />
		<eLabel position="253,163" size="30,16" zPosition="0" backgroundColor="#16000000" cornerRadius="2" />
		<eLabel position="288,162" size="1,18" zPosition="1" backgroundColor="#00505050" />
		<!-- <widget source="service" render="Cover" position="1140,478" size="104,156" zPosition="2" backgroundColor="#16000000" transparent="1" borderColor="#00203060" borderWidth="1" /> -->
	</screen>
	"""

	def __init__(self, session, curr_asset_url, curr_date_dt, prefetch_owner=None, zap_allowed=False):
		self.curr_asset_url = curr_asset_url
		self.curr_date_dt = curr_date_dt  # required...
		self.prefetch_owner = prefetch_owner
		self.zap_allowed = zap_allowed
		self.skin = self.skin.replace("{tvs_path}", tvglobals.PLUGINPATH).replace("{res}", tvglobals.RESOLUTION)
		Screen.__init__(self, session)
		self.IMPORTDICT = self.read_imported_file()  # load imported channel data
		self.asset_title, self.timestartend_str, self.span_starts_str = "", "", ""
		self.curr_service_ref, self.subline, self.trailer_data = "", "", ""
		self.timestart_dt = datetime.now(tz=None)
		self.spandurance_ts = 0
		self.data_bases = []
		self["release"] = StaticText(self.RELEASE)
		self["long_description"] = ScrollLabel()
		for widget in ["editorial", "conclusion", "repeat_hint", "credits", "imdb_rating", "timestartend",
						"title", "reviewdate", "channel_name", "key_info", "key_play"]:
			self[widget] = StaticText()
		for widget in ["picon", "image", "playbutton", "fsk", "is_top", "is_tip", "is_new",
						"is_live", "is_imdb", "is_tmdb", "has_timer", "thumb", "playpic"]:
			self[widget] = Pixmap()
			self[widget].hide()
		for index in range(5):
			self[f"ratinglabel{index}l"] = StaticText()
			self[f"ratinglabel{index}h"] = StaticText()
			self[f"ratingdots{index}"] = Pixmap()
			self[f"ratingdots{index}"].hide()
		for index in range(6):
			self[f"type_label{index}l"] = StaticText()
			self[f"type_label{index}h"] = StaticText()
			self[f"type_text{index}"] = StaticText()
		self["key_red"] = StaticText()
		self["key_green"] = StaticText("Timer hinzufügen")
		self["key_yellow"] = StaticText("EPG-Suche")
		self["key_blue"] = StaticText("Zap" if zap_allowed else "")
		self["actions"] = ActionMap(["OkCancelActions", "ButtonSetupActions"], {
			"ok": self.key_exit,
			"cancel": self.key_exit,
			"cross_left": self.key_up,
			"cross_right": self.key_down,
			"cross_up": self.key_up,
			"cross_down": self.key_down,
			"channelup": self.key_up,
			"channeldown": self.key_down,
			"play": self.play_trailer,
			"playpause": self.play_trailer,
			"info": self.key_info,
			"epg": self.key_info,
			"green": self.key_green,
			"blue": self.zapto_current,
			"yellow": self.open_epg_search
		}, -1)
		self.onLayoutFinish.append(self.layout_finished)

	def layout_finished(self):
		iconpath = self.ICONPATH
		for widget, iconfile in [
				("playpic", "play.png"), ("playbutton", "playbutton.png"), ("is_top", "top.png"), ("is_new", "new.png"),
				("is_tip", "tip.png"), ("is_live", "live.png"), ("has_timer", "timer.png"), ("is_imdb", "imdb.png"), ("is_tmdb", "tmdb.png")
			]:
			iconfile = join(iconpath, iconfile)
			if isfile(iconfile):
				self[widget].instance.setPixmapFromFile(iconfile)
			self[widget].hide()
		callInThread(self.show_asset_details, self.curr_asset_url, full_screen=True)

	def key_green(self):
		timestartend = self.timestartend_str.split(" | ")
		if len(timestartend) > 2:
			start_ts, end_ts = self.split_timespan(timestartend[2].split(" - "), self.timestart_dt)  # e.g. 'heute | 16.02.2026 | 20:15 - 23:30'
			self.build_record_timer(start_ts, end_ts, self.curr_service_ref, self.asset_title, self.subline, self.finishkey_green)

	def finishkey_green(self, answer):
		if answer and not isinstance(answer, bool) and answer[0]:  # special case for close recursive
			self.session.nav.RecordTimer.record(answer[1])
			self["has_timer"].show()
			callInThread(self.show_asset_details, self.curr_asset_url, full_screen=True)

	def key_up(self):
		self["long_description"].pageUp()

	def key_down(self):
		self["long_description"].pageDown()

	def key_exit(self):
		self.close(False)  # return to main menu


class TVTipsBox(Screen, tvglobals):
	skin = """
	<screen name="TVtips" position="50,center" size="410,264" flags="wfNoBorder" backgroundColor="#16000000" resolution="1280,720" title="TV Spielfilm Tipps">
		<eLabel position="0,0" size="410,264" backgroundColor="#00203060" zPosition="-2" />
		<eLabel position="2,2" size="406,260" zPosition="-2" />
		<eLabel position="2,38" size="406,2" backgroundColor="#0027153c,#00101093,black,horizontal" zPosition="10" />
		<eLabel position="2,234" size="406,2" backgroundColor="#0027153c,#00101093,black,horizontal" zPosition="10" />
		<eLabel position="2,2" size="406,36" backgroundColor=" black,#00203060,horizontal" zPosition="0" />
		<widget name="image" position="6,42" size="200,150" alphatest="blend" scaleFlags="scaleCenterBottom" zPosition="1" />
		<widget name="fsk" position="10,148" size="40,40" alphatest="blend" zPosition="2" />
		<widget name="picon" position="258,92" size="100,60" alphatest="blend" scaleFlags="scale" zPosition="1" />
		<widget name="has_timer" position="342,94" size="14,14" alphatest="blend" zPosition="1" />
		<widget source="channel_name" render="Label" position="212,42" size="194,48" font="Regular;20" halign="center" valign="center" foregroundColor="#92cbdf" backgroundColor="#16000000" transparent="1" />
		<widget source="time_infos" render="Label" position="212,156" size="194,24" font="Regular;18" backgroundColor="#16000000" transparent="1" halign="center" />
		<widget source="imdb_rating" render="Label" position="212,178" size="194,24" font="Regular;18" foregroundColor="grey" backgroundColor="#16000000" transparent="1" halign="center" />
		<widget source="headline" render="Label" position="36,2" size="342,36" font="Regular;24" wrap="ellipsis" backgroundColor="#16000000" zPosition="1" halign="center" valign="center" transparent="1" />
		<ePixmap position="8,10" size="24,20" pixmap="{tvs_path}/pics/{res}/icons/left.png" alphatest="blend" zPosition="1" />
		<ePixmap position="378,10" size="24,20" pixmap="{tvs_path}/pics/{res}/icons/right.png" alphatest="blend" zPosition="1" />
		<widget source="title" render="Label" position="10,204" size="396,28" font="Regular;20" wrap="ellipsis" foregroundColor="#0092cbdf" backgroundColor="#16000000" halign="left" valign="center" transparent="1" />
		<eLabel text="Genre:" position="10,236" size="66,24" font="Regular;18" backgroundColor="#16000000" transparent="1" halign="left" valign="center" />
		<widget source="genre" render="Label" position="68,236" size="286,24" font="Regular;18" backgroundColor="#16000000" transparent="1" halign="left" valign="center" />
		<widget source="category" render="Label" position="278,236" size="120,24" font="Regular;18" backgroundColor="#16000000" transparent="1" halign="right" valign="center" />
		<widget name="thumb" position="362,102" size="40,40" alphatest="blend" zPosition="1" />
		<widget name="is_top" position="222,96" size="28,14" alphatest="blend" zPosition="1" />
		<widget name="is_live" position="222,96" size="28,14" alphatest="blend" zPosition="1" />
		<widget name="is_tip" position="222,116" size="28,14" alphatest="blend" zPosition="1" />
		<widget name="is_new" position="222,136" size="28,14" alphatest="blend" zPosition="1" />
	</screen>
	"""

	def __init__(self, session):
		self.skin = self.skin.replace("{tvs_path}", tvglobals.PLUGINPATH).replace("{res}", tvglobals.RESOLUTION)
		Screen.__init__(self, session)
		for widget in ["headline", "title", "time_infos", "genre", "channel_name", "imdb_rating",
						"category", "imdb_rating", "editorial", "conclusion"]:
			self[widget] = StaticText()
		for widget in ["is_top", "is_tip", "is_new", "is_live", "thumb", "image", "fsk", "picon", "has_timer"]:
			self[widget] = Pixmap()
		self.is_visible = False
		self.was_visible = False

	def show_dialog(self):
		self.is_visible = True
		self.show()

	def hide_dialog(self):
		self.was_visible = self.is_visible
		self.is_visible = False
		self.hide()

	def get_is_visible(self):
		return self.is_visible

	def get_was_visible(self):
		return self.was_visible

	def set_text(self, widget, text):
		self[widget].setText(text)

	def set_widget_image(self, widget, pic_file):
		self[widget].instance.setPixmapFromFile(pic_file)

	def show_widget(self, widget):
		self[widget].show()

	def hide_widget(self, widget):
		self[widget].hide()


class TVInfoBox(Screen):
	skin = """
	<screen name="TVInfoBox" position="390,432" size="500,110" flags="wfNoBorder" resolution="1280,720" title="TV Spielfilm Infobox">
		<eLabel position="0,0" size="500,110" backgroundColor="#00203060" zPosition="-1" />
		<eLabel position="2,2" size="496,106" zPosition="-1" />
		<widget source="info" render="Label" position="5,5" size="490,100" font="Regular;24" halign="center" valign="center" />
	</screen>
	"""

	def __init__(self, session):
		Screen.__init__(self, session)
		self["info"] = StaticText()
		self.is_visible = False
		self.tvinfobox_timer = eTimer()
		self.tvinfobox_timer.callback.append(self.hide_dialog)

	def show_dialog(self, info, timeout=2500):
		self["info"].setText(info)
		self.is_visible = True
		self.show()
		self.tvinfobox_timer.start(timeout, True)

	def hide_dialog(self):
		self.tvinfobox_timer.stop()
		self.is_visible = False
		self.hide()

	def get_is_visible(self):
		return self.is_visible


class TVUpdate(Screen):
	skin = """
	<screen name="TVUpdate" position="820,250" size="410,182" flags="wfNoBorder" resolution="1280,720" title="TV Spielfilm EPG-Update">
		<eLabel name="tvs_bg" position="2,2" size="406,32" backgroundColor="black,#203060,horizontal" zPosition="-1" />
		<eLabel name="tvs_line" position="2,34" size="406,2" backgroundColor="#27153c,#101093,black,horizontal" zPosition="10" />
		<eLabel position="0,0" size="410,182" backgroundColor="#203060" zPosition="-3" />
		<eLabel position="2,2" size="406,178" backgroundColor="#10060613" zPosition="-2" />
		<widget source="headline" render="Label" position="10,2" size="400,32" font="Regular;24" transparent="1" halign="left" valign="center"/>
		<widget source="progress_hdr0" render="Label" position="10,38" size="390,28" font="Regular;18" wrap="ellipsis" transparent="1" valign="bottom" />
		<widget name="progressbar0" position="80,68" size="320,20" foregroundColor="#203060" zPosition="1" backgroundColor="#505050" />
		<widget source="progress_txt0" render="Label" position="10,70" size="68,16" font="Regular;14" foregroundColor="yellow" backgroundColor="#16000000" transparent="0" halign="center" valign="top" zPosition="2" />
		<eLabel position="8,68" size="72,20" zPosition="-1" backgroundColor="#324b96" />
		<widget source="progress_hdr1" render="Label" position="10,90" size="390,28" font="Regular;18" wrap="ellipsis" transparent="1" valign="bottom" />
		<widget name="progressbar1" position="80,120" size="320,20" foregroundColor="#203060" backgroundColor="#505050" />
		<widget source="progress_txt1" render="Label" position="10,122" size="68,16" font="Regular;14" foregroundColor="yellow" backgroundColor="#16000000" transparent="0" halign="center" valign="top" zPosition="2" />
		<eLabel position="8,120" size="72,20" zPosition="-1" backgroundColor="#324b96" valign="top" halign="center" />
		<widget source="key_yellow" render="Label" position="36,148" size="120,24" font="Regular;18" transparent="1" halign="left" valign="center"/>
		<widget source="key_blue" render="Label" position="176,148" size="160,24" font="Regular;18" transparent="1" halign="left" valign="center"/>
		<eLabel name="button_yellow" position="20,146" size="6,30" backgroundColor="#7a6213,#e6c619,vertical" zPosition="1" />
		<eLabel name="button_blue" position="160,146" size="6,30" backgroundColor="#101093,#4040ff,vertical" zPosition="1" />
	</screen>
	"""

	def __init__(self, session):
		Screen.__init__(self, session)
		self["headline"] = StaticText()
		for index in range(2):
			self[f"progress_hdr{index}"] = StaticText()
			self[f"progressbar{index}"] = ProgressBar()
			self[f"progress_txt{index}"] = StaticText()
		self["key_yellow"] = StaticText()
		self["key_blue"] = StaticText()
		self.is_visible = False
		self.was_visible = False

	def show_dialog(self):
		self.was_visible = False
		self.is_visible = True
		self.show()

	def hide_dialog(self):
		self.was_visible = self.is_visible
		self.is_visible = False
		self.hide()

	def get_was_visible(self):
		return self.was_visible

	def get_is_visible(self):
		return self.is_visible

	def set_range(self, widget, range):
		self[widget].setRange(range)

	def set_value(self, widget, value):
		self[widget].setValue(value)

	def set_text(self, widget, text):
		self[widget].setText(text)


class TVSettingsLocationBox(LocationBox):
	def __init__(self, session, curr_dir):
		inhibit = defaultInhibitDirs[:]
		inhibit.remove("/usr")
		inhibit.remove("/share")
		if curr_dir == "":
			curr_dir = None
		LocationBox.__init__(self, session, text="Wo sollen die TVS-EPG Daten zwischengespeichert werden (Cache)?", currDir=curr_dir, inhibitDirs=inhibit)
		self.skin_name = ["WeatherSettingsLocationBox", "LocationBox"]


class TVOverview(TVScreenHelper, Screen):
	skin = """
	<screen name="TVOverview" position="10,10" size="1260,700" flags="wfNoBorder" resolution="1280,720" backgroundColor="#16000000" title="TV Spielfilm Übersicht">
		<eLabel name="Gradient_BlueBlack" position="0,0" size="120,70" zPosition="-1" backgroundColor="#10060613" />
		<eLabel name="Gradient_BlueBlack" position="0,64" size="1260,590" zPosition="-10" backgroundColor="#10060613" />
		<eLabel position="0,0" size="1260,60" backgroundColor=" black,#00203060,horizontal" zPosition="1" />
		<eLabel position="0,60" size="1260,2" backgroundColor="#0027153c,#00101093,black,horizontal" zPosition="10" />
		<eLabel position="740,362" size="510,2" backgroundColor="#0027153c,#00101093,black,horizontal" zPosition="10" />
		<eLabel position="740,582" size="510,2" backgroundColor="#0027153c,#00101093,black,horizontal" zPosition="10" />
		<eLabel position="0,652" size="1260,2" backgroundColor="#0027153c,#00101093,black,horizontal" zPosition="10" />
		<ePixmap position="0,0" size="240,60" pixmap="{tvs_path}/pics/{res}/logos/TVSpielfilm.png" alphatest="blend" zPosition="13" />
		<widget source="release" render="Label" position="180,28" size="80,20" font="Regular;18" textBorderColor="#00505050" textBorderWidth="1" foregroundColor="#ffff00" backgroundColor="#16000000" valign="center" zPosition="12" transparent="1" />
		<widget source="reviewdate" render="Label" position="0,2" size="1260,30" font="Regular;20" foregroundColor="white" backgroundColor="#16000000" halign="center" valign="center" zPosition="12" transparent="1" />
		<widget name="prefetchpic" position="280,16" size="96,28" alphatest="blend" zPosition="13" />
		<widget name="prefetchbar" position="290,36" size="76,2" foregroundColor="#ffff00" backgroundColor="#505050" zPosition="14" />
		<widget name="progressbar" position="450,32" size="430,24" foregroundColor="#203060" backgroundColor="#505050" transparent="1" zPosition="8" />
		<widget source="progress_txt" render="Label" position="388,34" size="60,20" font="Regular;16" foregroundColor="yellow" backgroundColor="#16000000" transparent="0" halign="center" valign="center" zPosition="9" />
		<widget source="short_status" render="Label" position="452,34" size="426,20" font="Regular;16" foregroundColor="#ffffff" transparent="1" halign="left" valign="center" wrap="ellipsis" zPosition="9" />
		<eLabel position="8,1020" size="720,22" backgroundColor="grey" zPosition="-1" />
		<widget source="long_status" render="Label" conditional="long_status" position="0,30" size="1260,26" font="Regular;16" foregroundColor="#92cbdf" backgroundColor=" black,#00203060,horizontal" halign="center" valign="center" zPosition="10">
			<convert type="ConditionalShowHide" />
		</widget>
		<widget source="global.CurrentTime" render="Label" position="1110,0" size="140,60" font="Regular;46" noWrap="1" halign="center" valign="bottom" foregroundColor="white" backgroundColor="#16000000" zPosition="12" transparent="1">
			<convert type="ClockToText">Default</convert>
		</widget>
		<widget source="global.CurrentTime" render="Label" position="1000,2" size="100,26" font="Regular;16" noWrap="1" halign="right" valign="bottom" foregroundColor="white" backgroundColor="#16000000" zPosition="12" transparent="1">
			<convert type="ClockToText">Format:%A</convert>
		</widget>
		<widget source="global.CurrentTime" render="Label" position="1000,26" size="100,26" font="Regular;16" noWrap="1" halign="right" valign="bottom" foregroundColor="white" backgroundColor="#16000000" zPosition="12" transparent="1">
			<convert type="ClockToText">Format:%e. %B</convert>
		</widget>
		<widget source="menulist" render="Listbox" position="4,70" size="724,576" itemCornerRadiusSelected="6" itemGradientSelected=" black,#203060,black,horizontal" enableWrapAround="1" foregroundColorSelected="white" backgroundColor="#16000000" transparent="1" scrollbarMode="showOnDemand" scrollbarBorderWidth="1" scrollbarWidth="10" scrollbarBorderColor="blue" scrollbarForegroundColor="#203060">
			<convert type="TemplatedMultiContent">{"template": [
				MultiContentEntryPixmapAlphaBlend(pos=(6,4), size=(64,38), flags=BT_HALIGN_LEFT|BT_VALIGN_CENTER|BT_SCALE, png=1),  # picon
				MultiContentEntryText(pos=(78,0), size=(100,16), font=1, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER|RT_ELLIPSIS, color=0x00ffff, text=2),  # channel_name
				MultiContentEntryText(pos=(78,18), size=(100,16), font=1, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, color=0x00ff00, text=3),  # time
				MultiContentEntryProgress(pos=(80,38), size=(88,6), borderWidth=1, foreColor=0xcbcbcb, percent=-4),  # progressbar
				MultiContentEntryText(pos=(184,0), size=(456,26), font=0, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER|RT_ELLIPSIS, text=5),  # title
				MultiContentEntryText(pos=(184,22), size=(456,22), font=1, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER|RT_ELLIPSIS, text=6),  # info
				MultiContentEntryPixmapAlphaBlend(pos=(56,0), size=(14,14), flags=BT_HALIGN_LEFT|BT_VALIGN_CENTER, png=7),  # has_timer
				MultiContentEntryPixmapAlphaBlend(pos=(640,4), size=(40,40), flags=BT_HALIGN_LEFT|BT_VALIGN_CENTER, png=8),  # thumb
				MultiContentEntryPixmapAlphaBlend(pos=(680,0), size=(40,14), flags=BT_HALIGN_LEFT|BT_VALIGN_CENTER, png=9),  # first iconspace
				MultiContentEntryPixmapAlphaBlend(pos=(680,16), size=(40,14), flags=BT_HALIGN_LEFT|BT_VALIGN_CENTER, png=10),  # second iconspace
				MultiContentEntryPixmapAlphaBlend(pos=(680,32), size=(40,14), flags=BT_HALIGN_LEFT|BT_VALIGN_CENTER, png=11)  # third iconspace
				],
				"fonts": [gFont("Regular",20),gFont("Regular",16),gFont("Regular",14)],
				"itemHeight":48
				}
			</convert>
		</widget>
		<eLabel position="386,32" size="64,24" zPosition="8" backgroundColor="#203060" />
		<widget name="picon" position="760,210" size="148,88" alphatest="blend" scaleFlags="scale" zPosition="1" />
		<widget name="has_timer" position="892,212" size="14,14" alphatest="blend" zPosition="3" />
		<widget source="channel_name" render="Label" position="740,178" size="187,32" font="Regular;24" halign="center" foregroundColor="#92cbdf" backgroundColor="#16000000" transparent="1" />
		<widget name="image" position="936,66" size="320,240" alphatest="blend" scaleFlags="centerBottom" zPosition="1" />
		<widget name="playbutton" position="1060,160" size="60,60" alphatest="blend" zPosition="2" />
		<widget name="fsk" position="940,262" size="40,40" alphatest="blend" zPosition="2" />
		<widget name="is_tip" position="942,310" size="28,14" alphatest="blend" zPosition="1" />
		<widget name="is_new" position="976,310" size="28,14" alphatest="blend" zPosition="1" />
		<widget name="is_top" position="1010,310" size="28,14" alphatest="blend" zPosition="1" />
		<widget name="is_live" position="1010,310" size="28,14" alphatest="blend" zPosition="1" />
		<widget name="is_imdb" position="1044,310" size="28,14" alphatest="blend" zPosition="3" />
		<widget name="is_tmdb" position="1078,310" size="28,14" alphatest="blend" zPosition="3" />
		<widget source="imdb_rating" render="Label" position="1112,310" size="140,18" font="Regular;14" foregroundColor="yellow" halign="center" noWrap="1" zPosition="2" backgroundColor="#16000000" transparent="1" />
		<widget source="timestartend" render="Label" position="748,302" size="170,24" font="Regular;24" halign="center" valign="center" backgroundColor="#16000000" transparent="1" />
		<widget source="ratinglabel0l" render="Label" position="760,70" size="90,24" font="Regular;16" halign="right" valign="center" foregroundColor="#10333333" zPosition="0" backgroundColor="#16000000" transparent="1" />
		<widget source="ratinglabel0h" render="Label" position="760,70" size="90,24" font="Regular;16" halign="right" valign="center" foregroundColor="white" zPosition="0" backgroundColor="#16000000" transparent="1" />
		<widget name="ratingdots0" position="860,74" size="46,14" alphatest="blend" />
		<widget source="ratinglabel1l" render="Label" position="760,90" size="90,24" font="Regular;16" valign="center" halign="right" foregroundColor="#10333333" zPosition="0" backgroundColor="#16000000" transparent="1" />
		<widget source="ratinglabel1h" render="Label" position="760,90" size="90,24" font="Regular;16" valign="center" halign="right" foregroundColor="white" zPosition="0" backgroundColor="#16000000" transparent="1" />
		<widget name="ratingdots1" position="860,94" size="46,14" alphatest="blend" />
		<widget source="ratinglabel2l" render="Label" position="760,110" size="90,24" font="Regular;16" valign="center" halign="right" foregroundColor="#10333333" zPosition="0" backgroundColor="#16000000" transparent="1" />
		<widget source="ratinglabel2h" render="Label" position="760,110" size="90,24" font="Regular;16" valign="center" halign="right" foregroundColor="white" zPosition="0" backgroundColor="#16000000" transparent="1" />
		<widget name="ratingdots2" position="860,114" size="45,15" alphatest="blend" />
		<widget source="ratinglabel3l" render="Label" position="760,130" size="90,24" font="Regular;16" valign="center" halign="right" foregroundColor="#10333333" zPosition="0" backgroundColor="#16000000" transparent="1" />
		<widget source="ratinglabel3h" render="Label" position="760,130" size="90,24" font="Regular;16" valign="center" halign="right" foregroundColor="white" zPosition="0" backgroundColor="#16000000" transparent="1" />
		<widget name="ratingdots3" position="860,134" size="45,15" alphatest="blend" />
		<widget source="ratinglabel4l" render="Label" position="760,150" size="90,24" font="Regular;16" valign="center" halign="right" foregroundColor="#10333333" zPosition="0" backgroundColor="#16000000" transparent="1" />
		<widget source="ratinglabel4h" render="Label" position="760,150" size="90,24" font="Regular;16" valign="center" halign="right" foregroundColor="white" zPosition="0" backgroundColor="#16000000" transparent="1" />
		<widget name="ratingdots4" position="860,154" size="45,15" alphatest="blend" />
		<widget name="thumb" position="726,295" size="40,40" alphatest="blend" zPosition="2" />
		<widget source="editorial" render="Label" position="740,584" size="510,24" font="Regular;16" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
		<widget source="conclusion" render="Label" position="740,602" size="510,24" font="Regular;18" foregroundColor="#92cbdf" backgroundColor="#16000000" transparent="1" />
		<widget source="repeat_hint" render="RunningText" position="740,626" size="510,24" font="Regular;18" options="movetype=running,startpoint=0,startdelay=2000,wrap=0,always=0,repeat=2,oneshot=1" halign="left" noWrap="1" backgroundColor="#16000000" transparent="1" />
		<widget source="title" render="RunningText" options="movetype=running,startpoint=0,startdelay=2000,wrap=0,always=0,repeat=2,oneshot=1" position="740,330" size="510,32" font="Regular;24" halign="left" noWrap="1" foregroundColor="#92cbdf" backgroundColor="#16000000" transparent="1" />
		<widget source="long_description" render="RunningText" options="movetype=running,startdelay=6000,steptime=60,direction=top,startpoint=0,wrap=1,always=0,repeat=2,oneshot=1" position="740,366" size="510,214" font="Regular;21" backgroundColor="#16000000" transparent="1" />
		<eLabel name="button_red" position="10,660" size="6,36" backgroundColor="#00821c17,#00fe0000,vertical" zPosition="1" />
		<eLabel name="button_green" position="150,660" size="6,36" backgroundColor="#00006600,#0024a424,vertical" zPosition="1" />
		<eLabel name="button_yellow" position="256,660" size="6,36" backgroundColor="#007a6213,#00e6c619,vertical" zPosition="1" />
		<eLabel name="button_blue" position="362,660" size="6,36" backgroundColor="#101093,#4040ff,vertical" zPosition="1" />
		<widget source="key_red" render="Label" position="20,666" size="130,26" font="Regular;18" valign="center" halign="left" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
		<widget source="key_green" render="Label" position="160,666" size="96,26" font="Regular;18" valign="center" halign="left" wrap="ellipsis" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
		<widget source="key_yellow" render="Label" position="266,666" size="96,26" font="Regular;18" valign="center" halign="left" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
		<widget source="key_blue" render="Label" position="372,666" size="96,26" font="Regular;18" valign="center" halign="left" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
		<eLabel text="Tag -" position="414,666" size="54,26" font="Regular;18" valign="center" halign="right" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
		<ePixmap position="474,666" size="46,28" pixmap="{tvs_path}/pics/{res}/icons/ch_plus_minus.png" alphatest="blend" zPosition="1" />
		<eLabel text="Tag +" position="526,666" size="54,26" font="Regular;18" valign="center" halign="left" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
		<ePixmap position="600,666" size="46,28" pixmap="{tvs_path}/pics/{res}/icons/ok.png" alphatest="blend" zPosition="1" />
		<eLabel text="Details" position="650,666" size="100,26" font="Regular;18" valign="center" halign="left" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
		<eLabel text="Woche -" position="708,666" size="76,26" font="Regular;18" valign="center" halign="right" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
		<ePixmap position="788,666" size="46,28" pixmap="{tvs_path}/pics/{res}/icons/left0right.png" alphatest="blend" zPosition="1" />
		<eLabel text="Woche + " position="840,666" size="76,26" font="Regular;18" valign="center" halign="left" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
		<ePixmap position="920,666" size="76,28" pixmap="{tvs_path}/pics/{res}/icons/info_epg.png" alphatest="blend" zPosition="1" />
		<widget source="key_info" render="Label" position="1000,666" size="120,26" font="Regular;18" valign="center" halign="left" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
		<widget name="playpic" position="1094,666" size="20,28" alphatest="blend" zPosition="2" />
		<widget source="key_play" render="Label" position="1116,666" size="160,26" font="Regular;18" valign="center" halign="left" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
		<eLabel position="936,306" size="318,22" zPosition="-1" backgroundColor="#505050" cornerRadius="2" />
		<eLabel position="937,308" size="316,20" zPosition="0" backgroundColor="#16000000" cornerRadius="2" />
		<eLabel position="973,308" size="1,18" zPosition="10" backgroundColor="#505050" />
		<eLabel position="1007,308" size="1,18" zPosition="10" backgroundColor="#505050" />
		<eLabel position="1041,308" size="1,18" zPosition="10" backgroundColor="#505050" />
		<eLabel position="1075,308" size="1,18" zPosition="10" backgroundColor="#505050" />
		<eLabel position="1109,308" size="1,18" zPosition="10" backgroundColor="#505050" />
	</screen>
	"""

	def __init__(self, session, userspan, single_channel_id=""):
		self.session = session
		self.userspan = userspan
		(self.span_starts_str, self.time_code), self.spandurance_ts = userspan
		self.single_channel_id = single_channel_id
		self.skin = self.skin.replace("{tvs_path}", tvglobals.PLUGINPATH).replace("{res}", tvglobals.RESOLUTION)
		Screen.__init__(self, session)
		self.tvinfobox = session.instantiateDialog(TVInfoBox)
		self.filterindex = int(config.plugins.tvspielfilm.defaultfilter.value)
		self.filtersettings = loads(config.plugins.tvspielfilm.filtersettings.value)
		self.curr_date_dt = self.timestart_dt = datetime.now(tz=None)
		self.currday_delta, self.len_import_dict, self.total_assets_count = 0, 0, 0
		self.data_bases, self.skin_list, self.skin_dicts = [], [], []
		self.asset_title, self.trailer_data, self.curr_imdb_id, self.curr_tmdb_id = "", "", "", ""
		self.channel_name, self.curr_service_ref, self.curr_asset_url = "", "", ""
		self.load_all_epgactive, self.zap_allowed, self.prefetch_active = False, False, False
		self.key_exit_pressed, self.load_all_epgstop = False, False
		self["release"] = StaticText(self.RELEASE)
		for wname in [
			"reviewdate", "long_status", "progress_txt", "short_status", "channel_name", "timestartend", "imdb_rating",
			"repeat_hint", "title", "editorial", "conclusion", "long_description", "key_info", "key_play"
			]:
			self[wname] = StaticText()
		for wname in [
			"prefetchpic", "picon", "image", "playbutton", "is_top", "is_tip", "is_new",
			"is_live", "is_imdb", "is_tmdb", "has_timer", "thumb", "fsk", "playpic"
			]:
			self[wname] = Pixmap()
			self[wname].hide()
		for index in range(5):
			self[f"ratinglabel{index}l"] = StaticText()
			self[f"ratinglabel{index}h"] = StaticText()
			self[f"ratingdots{index}"] = Pixmap()
			self[f"ratingdots{index}"].hide()
		self["progressbar"] = ProgressBar()
		self["prefetchbar"] = ProgressBar()
		self["prefetchbar"].hide()
		self["menulist"] = List()
		self["menulist"].onSelectionChanged.append(self.show_current_asset)
		self["key_red"] = StaticText()
		self["key_green"] = StaticText("Timer")
		self["key_yellow"] = StaticText("EPG-Suche")
		self["key_blue"] = StaticText()
		self["actions"] = ActionMap(["OkCancelActions", "ButtonSetupActions"], {
			"ok": self.key_ok,
			"cancel": self.key_exit,
			"play": self.play_trailer,
			"playpause": self.play_trailer,
			"red": self.key_red,
			"red_long": self.key_redlong,
			"channeldown": self.prev_day,
			"channelup": self.next_day,
			"previous": self.prev_week,
			"next": self.next_week,
			"info": self.key_info,
			"epg": self.key_info,
			"green": self.key_green,
			"yellow": self.open_epg_search,
			"blue": self.zapto_current
		}, -1)
		self.IMPORTDICT = self.read_imported_file()  # load imported channel data
		iconpath = self.ICONPATH
		self.icon_top = LoadPixmap(cached=True, path=join(iconpath, "top.png"))
		self.icon_tip = LoadPixmap(cached=True, path=join(iconpath, "tip.png"))
		self.icon_new = LoadPixmap(cached=True, path=join(iconpath, "new.png"))
		self.icon_live = LoadPixmap(cached=True, path=join(iconpath, "live.png"))
		self.icon_time = LoadPixmap(cached=True, path=join(iconpath, "timer.png"))
		self.init_asset_prefetch()
		self.onLayoutFinish.append(self.layout_finished)

	def layout_finished(self):
		self["key_red"].setText(f"Filter: {self.filtersettings[self.filterindex][0][0].replace('Unterhaltung', 'Unterhalt.')}")
		iconpath = self.ICONPATH
		for widget, iconfile in [
			("prefetchpic", "prefetch.png"), ("is_top", "top.png"), ("is_new", "new.png"), ("is_tip", "tip.png"),
			("is_live", "live.png"), ("has_timer", "timer.png"), ("is_imdb", "imdb.png"), ("is_tmdb", "tmdb.png"),
			("playpic", "play.png"), ("playbutton", "playbutton.png")
			]:
			iconfile = join(iconpath, iconfile)
			if isfile(iconfile):
				self[widget].instance.setPixmapFromFile(iconfile)
			self[widget].hide()
		callInThread(self.load_all_epg)

	def set_progress_values(self, index, valuelist):
		self.call_gui(self.set_progress_values_ui, index, valuelist)

	def set_progress_values_ui(self, index, valuelist):
		self["progressbar"].setValue(valuelist[1])
		self["progress_txt"].setText(valuelist[2])
		self["short_status"].setText(valuelist[0])

	def load_all_epg(self):  # threaded function
		self.load_all_epgactive = True
		self.call_gui(self.set_reviewdate, "", False)
		self.total_assets_count = 0
		self.update_widget("long_status", "setText", "")
		import_dict = self.IMPORTDICT
		self.len_import_dict = len(import_dict)
		self.update_widget("progressbar", "setRange", (0, self.len_import_dict))
		self.update_widget("progressbar", "setValue", 0)
		self.update_widget("progress_txt", "setText", f"1/{self.len_import_dict}" if self.time_code == 'now' else "1/1")
		if self.single_channel_id:  # 'singleChannel' means 'the entire day' from 05:00h to 05:00h next morning
			self.channel_name = import_dict.get(self.single_channel_id, ["", "{unbekannt}"])[1]
		self.assets_count, self.all_images_count = 0, 0
		self.skin_dicts, self.skin_list = [], []
		span_starts_dt, span_ends_dt = self.get_startsends_dt(self.curr_date_dt, self.span_starts_str, self.spandurance_ts, self.single_channel_id, self.time_code)
		err_msg, assets = "", []
		if self.single_channel_id:
			err_msg, assets = self.get_channel_assets(span_starts_dt, self.single_channel_id)
		else:
			server_day = self.get_tvs_server_day(span_starts_dt)
			server_days = []
			while server_day < span_ends_dt:
				server_days.append(server_day)
				server_day += timedelta(days=1)
			work_total = len(import_dict) * len(server_days)
			self.update_widget("progressbar", "setRange", (0, work_total))
			work_done = 0
			for server_day in server_days:
				for channel_id, channel_info in import_dict.items():
					if self.load_all_epgstop:
						break
					err_msg, channel_assets = self.get_channel_assets(server_day, channel_id)
					if err_msg:
						print(f"[{self.MODULE_NAME}] ERROR: load_all_epg could not load channel cache for '{channel_id}'.")
						break
					assets.extend(channel_assets)
					work_done += 1
					self.set_progress_values(1, (f"Sender: '{channel_info[1]}'", work_done, f"{work_done}/{work_total}"))
				if err_msg or self.load_all_epgstop:
					break
		if self.load_all_epgstop:
			print(f"[{self.MODULE_NAME}] TVS-EPG download was stopped on user demand.")
		else:
			self.create_skin_list(assets, span_starts_dt, span_ends_dt)  # build the skin list in the worker
		self.call_gui(self.refresh_skin_list)
		self.call_gui(self.set_longstatus)
		self.update_widget("progressbar", "setValue", 0)
		self.update_widget("progress_txt", "setText", "")
		self.update_widget("short_status", "setText", "")
		self.call_gui(self.show_current_asset)
		self.load_all_epgstop, self.load_all_epgactive = False, False
		if self.key_exit_pressed:
			self.call_gui(self.key_exit)  # return to the main menu on the GUI thread
		else:
			self.start_asset_prefetch([entry[0] for entry in self.skin_list if entry[0] and entry[0] != self.curr_asset_url])

	def set_reviewdate(self, timestartend, full_screen=False):
		self.curr_date_dt = datetime.now(tz=None) + timedelta(days=self.currday_delta)
		span_starts_dt, span_ends_dt = self.get_startsends_dt(self.curr_date_dt, self.span_starts_str, self.spandurance_ts, self.single_channel_id, self.time_code)
		span_starts_str, span_ends_str = span_starts_dt.strftime("%H:%M"), span_ends_dt.strftime("%H:%M")
		dateonly_dt = span_starts_dt.date()
		now_dateonly_dt = datetime.now(tz=None).date()
		day_names = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
		weekday = "heute" if now_dateonly_dt == dateonly_dt else day_names[span_starts_dt.weekday()]
		if timestartend:
			start_str, end_str = timestartend.split(" - ")
			titlelen_str = f"{int((datetime.strptime(end_str, '%H:%M') - datetime.strptime(start_str, '%H:%M')).seconds / 60)} Minuten"
			timestartend = f"{timestartend} Uhr"
		else:
			titlelen_str = ""
		if full_screen:
			reviewdate = " | ".join(list(filter(None, [f"{weekday} {span_starts_dt.strftime('%d.%m.%Y')}", f"{timestartend}", f"{titlelen_str}"])))
		else:
			# correct dayDelta if span starts next day
			corr_day_delta = self.currday_delta if span_starts_dt.day == self.curr_date_dt.day else self.currday_delta + 1
			if self.single_channel_id:  # 'singleChannel' means 'the entire day' from 05:00h to 05:00h next morning
				currweekday, next_weekday = day_names[dateonly_dt.weekday()][:2], day_names[(dateonly_dt + timedelta(days=1)).weekday()][:2]
				reviewdate = " | ".join(list(filter(None, [f"{weekday} {span_starts_dt.strftime('%d.%m.%Y')}", f"{corr_day_delta:+} Tag(e)", f"{currweekday} 05:00 - {next_weekday} 05:00 Uhr"])))
			elif self.time_code == "now":
				now_dt = datetime.now(tz=None)
				reviewdate = f"jetzt {now_dt.strftime('%d.%m.%Y')} | {now_dt.strftime('%H:%M')} - {span_ends_str} Uhr"
			else:
				reviewdate = f"{weekday} {span_starts_dt.strftime('%d.%m.%Y')} | {corr_day_delta:+} Tag(e) | {span_starts_str} - {span_ends_str} Uhr"
		self["reviewdate"].setText(reviewdate)

	def create_skin_list(self, assets, span_starts_dt, span_ends_dt):
			skin_dicts = []
			import_dict = self.IMPORTDICT.keys()
			cat_filters = {value: key for key, value in tvspassets.cat_filters.items()}  # swap dict
			for asset in assets:
				if self.load_all_epgstop:
					break
				channel_id = asset.get("channel_id", "").lower()
				timestart_iso = asset.get("time_start", "")
				timestart_dt = datetime.fromisoformat(timestart_iso).replace(tzinfo=None) if timestart_iso else span_starts_dt
				timeend_iso = asset.get("time_end", "")
				timeend_dt = datetime.fromisoformat(timeend_iso).replace(tzinfo=None) if timeend_iso else span_ends_dt
				now_dt = datetime.now(tz=None)
				progress = -1
				if timeend_dt:
					durance = timeend_dt - timestart_dt
					progress = int(((now_dt - timestart_dt) / durance) * 100) if durance else -1
				asset_in_span = timestart_dt >= span_starts_dt and timestart_dt < span_ends_dt
				if self.single_channel_id or channel_id in import_dict and asset_in_span:  # channel has been imported and starts within the time span
					asset_url = asset.get("asset_url", "")
					category = asset.get("category", "")  # e.g. 'SP' for 'Spielfilm'
					title = asset.get("title", "")
					genre = asset.get("genre", "")  # e.g. 'Katastrophenaction'
					timespan_ts = (int(timestart_dt.timestamp()), int(timeend_dt.timestamp()))
					channel_name = asset.get("channel_name", "") if config.plugins.tvspielfilm.channel_name.value else self.IMPORTDICT.get(channel_id, ["", "{unbekannt}"])[1]
					info = " | ".join(filter(None, [genre, cat_filters.get(category, ""), f"{asset.get('country_year', '')}"]))
					thumb_id_numeric = asset.get("thumb_id_numeric", 0)
					is_top = asset.get("is_top", False)
					is_tip = asset.get("is_tip", False)
					is_new = asset.get("is_new", False)
					is_live = asset.get("is_live", False)
					sref = self.IMPORTDICT.get(channel_id, ["", ""])[0]
					skin_dict = {"asset_url": asset_url, "channel_id": channel_id, "channel_name": channel_name, "sref": sref, "timespan_ts": timespan_ts,
								"progress": progress, "title": title, "info": info, "category": category, "genre": genre,
								"thumb_id_numeric": thumb_id_numeric, "is_top": is_top, "is_tip": is_tip, "is_new": is_new, "is_live": is_live}
					skin_dicts.append(skin_dict)
					self.total_assets_count += 1
			self.skin_dicts = skin_dicts

	def set_longstatus(self):
		len_skin_dicts = len(self.skin_dicts)
		msg = f"{len_skin_dicts} Einträge"
		if self.single_channel_id:  # singleChannel means 'the entire day' from 05:00h to 05:00h next morning
			channel_name = self.channel_name if config.plugins.tvspielfilm.channel_name.value else self.IMPORTDICT.get(self.single_channel_id, ["", "{unbekannt}"])[1]
			msg += f" im Sender '{channel_name}' gefunden."
		else:
			msg += f" in insgesamt {self.len_import_dict} Sendern gefunden."
			lenskin_list = len(self.skin_list)
			if len_skin_dicts != lenskin_list:
				msg += f" Gefilterte Einträge: {lenskin_list}"
		self["long_status"].setText(msg)

	def refresh_skin_list(self):
		skin_list = []
		entry_counter, new_index = 0, -1
		timer_list = set(self.get_timer_list())
		old_index = self["menulist"].getCurrentIndex()
		currfilter = self.filtersettings[self.filterindex]
		for asset in self.skin_dicts:
			if self.load_all_epgstop:
				break
			if currfilter[0][1]:  # is a filter set?
				leaveout = True
				if currfilter[0][1] == "thumb" and asset.get("thumb_id_numeric"):
					leaveout = False
				elif currfilter[0][1] in tvspassets.cat_filters.values():
					if currfilter[0][1] == asset.get("category"):  # e.g. 'SP' for 'Spielfilm'
						leaveout = False
				else:
					for asset_flag in [(asset.get("is_tip"), "is_tip"), (asset.get("is_top"), "is_top"), (asset.get("is_new"), "is_new"), (asset.get("is_live"), "is_live")]:
						if asset_flag[0] and asset_flag[1] == currfilter[0][1]:
							leaveout = False
							break
				if leaveout:
					continue
			asset_url, channel_name, timespan_ts = asset.get("asset_url"), asset.get("channel_name"), asset.get("timespan_ts")
			progress, title, info, sref = asset.get("progress"), asset.get("title"), asset.get("info"), asset.get("sref")
			if new_index == -1 and asset_url == self.curr_asset_url:  # newIndex is not set yet and asset is still active?
				new_index = entry_counter
			if new_index == -1 and self.single_channel_id and progress > -1 and progress < 101:  # newIndex is not set yet and 'Senderübersicht' and transmission is currently on air?
				new_index = entry_counter
			has_timer = self.is_already_listed(asset.get("timespan_ts"), asset.get("sref"), timer_list)
			timer_pic = self.icon_time if has_timer else None
			picon_file = self.get_picon_file(asset.get("channel_id"))
			piconpix = LoadPixmap(cached=True, path=picon_file) if picon_file and isfile(picon_file) else None
			timespan = f"{datetime.fromtimestamp(timespan_ts[0]).strftime('%H:%M')} - {datetime.fromtimestamp(timespan_ts[1]).strftime('%H:%M')}"
			thumb_id_numeric = asset.get('thumb_id_numeric')
			thumb = LoadPixmap(cached=True, path=join(self.ICONPATH, f"thumb{thumb_id_numeric}.png")) if thumb_id_numeric else None
			offset = 0
			icons = [None, None, None]
			if asset.get("is_top"):
				icons[offset] = self.icon_top
				offset += 1
			elif asset.get("is_live"):
				icons[offset] = self.icon_live
				offset += 1
			if asset.get("is_tip"):
				icons[offset] = self.icon_tip
				offset += 1
			if asset.get("is_new"):
				icons[offset] = self.icon_new
			skin_list.append((asset_url, piconpix, channel_name, timespan, progress, title, info, timer_pic, thumb, *icons, sref))
			self.skin_list = skin_list
			entry_counter += 1
		if not skin_list:
			skin_list.append(("", None, "", "", -1, "keine Einträge gefunden", f"Der Filter '{currfilter[0][0]}' liefert für diesen Zeitraum kein Ergebnis.", None, None, *[None, None, None], ""))
			self.skin_list = []
			self.hide_asset_details()
		self["menulist"].updateList(skin_list)
		self["menulist"].setCurrentIndex(min(old_index, len(self.skin_list) - 1) if new_index == -1 else new_index)

	def show_current_asset(self):
		if self.skin_list:
			curr_index = min(self["menulist"].getCurrentIndex(), len(self.skin_list) - 1)
			asset_url = self.skin_list[curr_index][0]
			progress = self.skin_list[curr_index][4]
			self.zap_allowed = progress > -1 and progress < 101  # progressbar visible means: transmission is currently on air
			self["key_blue"].setText("Zap" if self.zap_allowed else "")
			if asset_url != self.curr_asset_url:  # is a new asset?
				self["image"].hide()
				self.hide_asset_details()
				self.curr_asset_url = asset_url  # set new asset_url
				callInThread(self.show_asset_details, asset_url, full_screen=False)
		else:
			self["image"].hide()
			self.hide_asset_details()

	def key_ok(self):
		if self.skin_list:
			curr_index = min(self["menulist"].getCurrentIndex(), len(self.skin_list) - 1)
			curr_asset_url = self.skin_list[curr_index][0]
			self.session.openWithCallback(self.key_ok_return, TVFullscreen, curr_asset_url, self.curr_date_dt, prefetch_owner=self, zap_allowed=self.zap_allowed)

	def key_ok_return(self, answer):
		if answer:
			self.close(True)  # close plugin (e.g. after zap)
		self.refresh_skin_list()  # required if a timer has been set in TVFullscreen

	def key_red(self):
		while True:  # use next active filter
			self.filterindex = (self.filterindex + 1) % len(self.filtersettings)
			if self.filtersettings[self.filterindex][1]:
				break
		self.key_redfinish()

	def key_redlong(self):
		active_filters = [(filter[0][0], filter[0][1]) for filter in self.filtersettings if filter[1]]
		msgtext = "Wähle einen der aktiven Filter:"
		self.session.openWithCallback(self.key_redlong_cb, ChoiceBox, list=active_filters, keys=[], title=msgtext)

	def key_redlong_cb(self, answer):
		if answer:
			answer = [answer, True]
			self.filterindex = self.filtersettings.index(answer)
			self.key_redfinish()

	def key_redfinish(self):
		self["key_red"].setText(f"Filter: {self.filtersettings[self.filterindex][0][0].replace('Unterhaltung', 'Unterhalt.')}")
		self.refresh_skin_list()
		self.show_current_asset()
		self.start_asset_prefetch([entry[0] for entry in self.skin_list if entry[0] and entry[0] != self.curr_asset_url])
		if not self.load_all_epgactive:
			self.set_longstatus()

	def key_green(self):
		current = self["menulist"].getCurrent()
		if current and len(current) > 12:
			start_ts, end_ts = self.split_timespan(current[3].split(" - "), datetime.now(tz=None) + timedelta(days=self.currday_delta))  # e.g. '20:15 - 21:45' or 'heute | 20:15'
			title, shortdesc, sref = current[5], current[6], current[12]
			self.build_record_timer(start_ts, end_ts, sref, title, shortdesc, self.finishkey_green)

	def finishkey_green(self, answer):
		if answer and not isinstance(answer, bool) and answer[0]:  # special case for close recursive
			self.session.nav.RecordTimer.record(answer[1])
			self["has_timer"].show()
			self["key_green"].setText("")
			self.refresh_skin_list()
			self.show_current_asset()

	def prev_day(self):
		self.change_day(-1)

	def prev_week(self):
		self.change_day(-7)

	def next_day(self):
		self.change_day(1)

	def next_week(self):
		self.change_day(7)

	def change_day(self, jump_days):
		if self.time_code != "now":
			desired_day = self.currday_delta + jump_days
			if self.load_all_epgactive:  # thread is still running
				self.load_all_epgstop = True
				self.tvinfobox.show_dialog("Laufender Prozess 'Lade TVS-EPG Daten' wird zuerst beendet.\nBitte gleich nochmal versuchen.")
			elif desired_day > 13:
				self.tvinfobox.show_dialog("Zukünftige TVS-EPG Daten nur bis\nmaximal +13 Tage im Voraus verfügbar.")
			elif desired_day < -7:
				self.tvinfobox.show_dialog("Vergangene TVS-EPG Daten nur bis\nmaximal -7 Tage im Nachhinein verfügbar.")
			else:
				self.currday_delta += jump_days
				callInThread(self.load_all_epg)
		else:
			self.tvinfobox.show_dialog("'Jetzt im TV' kann nur die Daten des aktuellen Zeitraums darstellen.")

	def key_exit(self):
		if self.prefetch_active and not self.load_all_epgactive:
			self.abort_asset_prefetch()
		elif self.load_all_epgactive or self.prefetch_active:
			self.key_exit_pressed = True  # instruct threads to break and return to main menu
			self.load_all_epgstop = True
		else:
			self.close(False)  # return to main menu


class TVMain(TVScreenHelper, Screen):
	skin = """
	<screen name="TVMain" position="center,center" size="320,570" resolution="1280,720" backgroundColor="#16000000" flags="wfNoBorder" title="TV Spielfilm Hauptmenü">
		<ePixmap position="0,0" size="220,60" pixmap="{tvs_path}/pics/{res}/logos/TVSpielfilm.png" alphatest="blend" zPosition="13" />
		<eLabel position="0,0" size="320,570" backgroundColor="#00203060" zPosition="-2" />
		<eLabel position="2,2" size="316,566" zPosition="-1" />
		<eLabel position="2,2" size="316,58" backgroundColor=" black,#00203060,horizontal" zPosition="1" />
		<eLabel position="2,60" size="316,2" backgroundColor="#0027153c,#00101093,black,horizontal" zPosition="10" />
		<widget source="release" render="Label" position="180,28" size="80,20" font="Regular;18" textBorderColor="#505050" textBorderWidth="1" foregroundColor="#00ffff00" backgroundColor="#16000000" valign="center" zPosition="12" transparent="1" />
		<widget source="mainmenu" render="Listbox" position="2,60" size="316,440" itemCornerRadiusSelected="4" itemGradientSelected="#051a264d,#10304070,#051a264d,horizontal" enableWrapAround="1" foregroundColorSelected="white" backgroundColor="#16000000" transparent="1" scrollbarMode="showOnDemand">
			<convert type="TemplatedMultiContent">{"template": [
				MultiContentEntryText(pos=(0,0), size=(316,40), font=0, color=0xffffff, flags=RT_HALIGN_CENTER|RT_VALIGN_CENTER, text=0)  # menutext
				],
				"fonts": [gFont("Regular",24)],
				"itemHeight":40
				}
			</convert>
		</widget>
		<eLabel position="2,502" size="316,2" backgroundColor="#0027153c,#00101093,black,horizontal" zPosition="10" />
		<eLabel text="von Mr.Servo - Skin von stein17 " position="0,506" size="320,18" font="Regular;14" foregroundColor="#0092cbdf" backgroundColor="#00000000" transparent="1" zPosition="2" halign="center" />
		<eLabel name="button_red" position="70,534" size="6,30" backgroundColor="#00821c17,#00fe0000,vertical" zPosition="1" />
		<widget source="key_red" render="Label" position="84,538" size="70,24" font="Regular;18" foregroundColor="#00ffffff" backgroundColor="#00000000" valign="center" transparent="1" zPosition="2" />
		<eLabel name="button_green" position="150,534" size="6,30" backgroundColor="#00006600,#0024a424,vertical" zPosition="1" />
		<widget source="key_green" render="Label" position="164,538" size="140,24" font="Regular;18" foregroundColor="#00ffffff" backgroundColor="#00000000" valign="center" transparent="1" zPosition="1" />
		<ePixmap position="10,536" size="46,28" pixmap="{tvs_path}/pics/{res}/icons/menu.png" alphatest="blend" zPosition="1" />
		<eLabel position="2,527" size="316,2" backgroundColor="#0027153c,#00101093,black,horizontal" zPosition="10" />
	</screen>
	"""

	def __init__(self, session):
		self.session = session
		self.skin = self.skin.replace("{tvs_path}", tvglobals.PLUGINPATH).replace("{res}", tvglobals.RESOLUTION)
		Screen.__init__(self, session)
		self.tvupdate = session.instantiateDialog(TVUpdate)
		self.tvinfobox = session.instantiateDialog(TVInfoBox)
		self.tvtipsbox = session.instantiateDialog(TVTipsBox)
		self.tips_allow, self.create_tips_active = True, False
		self.webaccess_check_abandoned = False
		self.webaccess_check_generation = 0
		self.tips_dicts = []
		self.single_channel_id, self.curr_asset_url = "", ""
		self.time_code = ""  # is required for self.assets_filepath
		self.curr_tip_cnt, self.currday_delta = 0, 0
		self.curr_date_dt = datetime.now(tz=None)
		self.tipsbox_timer = eTimer()
		self.tipsbox_timer.callback.append(self.tips_slideshow)
		self.old_channel_name = config.plugins.tvspielfilm.channel_name.value
		self["release"] = StaticText(self.RELEASE)
		self["mainmenu"] = List()
		self["key_red"] = StaticText("Import")
		self["key_green"] = StaticText()
		self["actions"] = ActionMap(["WizardActions", "ColorActions", "MenuActions"], {
			"ok": self.key_ok,
			"back": self.exit,
			"right": self.force_next_tip,
			"left": self.force_prev_tip,
			"down": self.down,
			"up": self.up,
			"red": self.key_red,
			"green": self.key_green,
			"yellow": self.key_yellow,
			"blue": self.key_blue,
			"menu": self.config
		}, -1)
		self.IMPORTDICT = self.read_imported_file()  # load imported channel data
		self.onShown.append(self.layout_finished)

	def layout_finished(self):
		err_msg, outdated = self.check_outdated_structure()
		if outdated:
			self.remove_cache_paths()
			msgtext = "Aufgrund einer neuen Datenstruktur wurde der\nTVS-EPG Zwischenspeicher (=Cache) gelöscht!\n\nMöchten Sie jetzt ein neues TVS-EPG Datenupdate durchführen?"
			self.session.openWithCallback(self.layout_finished_cb, MessageBox, msgtext, MessageBox.TYPE_YESNO, timeout=30, default=True)
		if self.create_cache_paths():
			self.exit()
		self.cleanup_cache()
		if self.update_mappingfile():
			self.tvinfobox.show_dialog("Die Sender-Zuweisungstabelle\n'/etc/enigma2/tvspielfilm/tvs_mapping.txt'\nwurde aktualisiert.", 5000)
		iconpath = self.ICONPATH
		for widget, iconfile in [
				("is_top", "top.png"), ("is_tip", "tip.png"), ("is_new", "new.png"),
				("is_live", "live.png"), ("has_timer", "timer.png")
			]:
			iconfile = join(iconpath, iconfile)
			if isfile(iconfile):
				self.tvtipsbox.set_widget_image(widget, iconfile)
		self.tvupdate.set_text("headline", "Sammle TVS-EPG Daten")
		self.tvupdate.set_text("key_yellow", "Abbruch")
		self.tvupdate.set_text("key_blue", "Ein-/Ausblenden")
		self.build_main_menu()
		if not self.webaccess_check_generation:
			self.webaccess_check_generation += 1
			generation = self.webaccess_check_generation
			setattr(tvspglobals, "WEBACCESS", None)
			self.tvinfobox.show_dialog("Prüfe TV Spielfilm Serverzugang ...", 30000)
			deferToThread(self.check_server_access).addCallbacks(
				self.check_webaccess_done, self.check_webaccess_failed,
				callbackArgs=(generation,), errbackArgs=(generation,))

	def check_server_access(self):
		class ImageFinder(HTMLParser):
			def __init__(self):
				super().__init__()
				self.image_url = ""

			def handle_starttag(self, tag, attrs):
				if tag == "img" and not self.image_url:
					for name, value in attrs:
						if name in ("src", "data-src") and value and urlsplit(value).hostname == "img.tvspielfilm.de":
							self.image_url = value
							break

		err_msg, html = tvsphelper.get_html_data(tvspglobals.WEBURL)
		if err_msg:
			return err_msg
		finder = ImageFinder()
		finder.feed(html)
		if not finder.image_url:
			return "Bildserver konnte nicht geprüft werden"
		err_msg, image = tvsphelper.get_binary_data(finder.image_url)
		if err_msg:
			return err_msg
		if not image or image.lstrip().lower().startswith((b"<!doctype html", b"<html", b"<head", b"<body")):
			return "Bildserver lieferte kein Bild"
		return ""

	def check_webaccess_done(self, err_msg, generation):
		if getattr(self, "webaccess_check_abandoned", True) or generation != self.webaccess_check_generation:
			return
		setattr(tvspglobals, "WEBACCESS", not bool(err_msg))
		self.tvinfobox.hide_dialog()
		if err_msg:
			msg_text = ("Der TV Spielfilm Server oder Bildserver ist nicht erreichbar.\n\n"
				"Bitte Internetverbindung und VPN-Zugang prüfen.\n"
				"Bei Verwendung eines VPN gegebenenfalls den VPN-Server wechseln.")
			self.session.open(MessageBox, msg_text, type=MessageBox.TYPE_ERROR, timeout=10, close_on_any_key=True)
		if tvspglobals.WEBACCESS:
			callInThread(self.get_tips)

	def check_webaccess_failed(self, failure, generation):
		return self.check_webaccess_done(failure.getErrorMessage() or "Serverprüfung fehlgeschlagen", generation)

	def layout_finished_cb(self, answer):
		if answer and tvspglobals.WEBACCESS:
			callInThread(self.update_future_epg)

	def build_main_menu(self):
		magazine = ("TV Magazin", 0, (("", ""), 0))
		spanmenu = ((f"{userspan[0][0]} im TV", index + 1, userspan) for index, userspan in enumerate(self.get_user_timespans()))  # build userspan menu
		usermenu = [*spanmenu, magazine] if config.plugins.tvspielfilm.first_item.value else [magazine, *spanmenu]
		usermenu.extend(
			[
			("Jetzt im TV", 5, (("", "now"), config.plugins.tvspielfilm.durance_n.value)),
			("laufende Sendung", 6, (("", ""), 0)),
			("Senderübersicht", 7, (("", ""), 0)),
			("TVS-EPG Daten updaten", 8, (("", ""), 0)),
			("TVS-EPG Daten löschen", 9, (("", ""), 0))
			]
		)
		self["mainmenu"].updateList(usermenu)

	def hide_main_dialogs(self):
		self.hide_tvtipsbox()
		self.tvupdate.hide_dialog()

	def webaccess_ready(self):
		if tvspglobals.WEBACCESS:
			return True
		self.tvinfobox.show_dialog("Der Serverzugang wird noch geprüft." if tvspglobals.WEBACCESS is None else "Kein Zugang zu TV Spielfilm. Bitte Internetverbindung und VPN prüfen.")
		return False

	def key_ok(self):
		if not (current := self["mainmenu"].getCurrent()):
			return
		if not self.webaccess_ready():
			return
		if current[1] == 0:  # TV magazine
			self.hide_main_dialogs()
			self.session.openWithCallback(self.return_ok1, TVMagazine)
		elif current[1] in [1, 2, 3, 4, 5]:  # user time spans
			self.hide_main_dialogs()
			self.session.openWithCallback(self.return_ok1, TVOverview, current[2])
		elif current[1] == 6:  # current program
			self.hide_main_dialogs()
			callInThread(self.get_current_asset_url, self.session.nav.getCurrentlyPlayingServiceOrGroup(), self.return_ok4)
		elif current[1] == 7:  # channels overview
			self.hide_main_dialogs()
			self.session.openWithCallback(self.return_ok1, TVSelectChannelCategory)
		elif current[1] == 8:  # TVS-EPG data update
			if TVS_UPDATEACTIVE or tvglobals.AUTOUPDATEACTIVE:
				self.tvinfobox.show_dialog("Das TVS-EPG Datenupdate oder Autoupdate läuft gerade.\nDer TVS-EPG Datenupdate kann daher im Moment nicht gestartet werden.")
			else:
				self.hide_main_dialogs()
				msgtext = "TVS-EPG Datenupdate (nur für aktivierte Zeiträume) durchführen?"
				choicelist = [("Abbruch", 0), ("Ergänze nur die fehlenden Datensätze", 1), ("Überschreibe nur die Datensätze des heutigen Tages", 2), ("Überschreibe alle bereits vorhandene Datensätze", 3)]
				self.session.openWithCallback(self.return_ok2, ChoiceBox, list=choicelist, keys=[], title=msgtext)
		elif current[1] == 9:  # delete TVS-EPG cache
			if TVS_UPDATEACTIVE or tvglobals.AUTOUPDATEACTIVE:
				self.tvinfobox.show_dialog("Das TVS-EPG Datenupdate oder Autoupdate läuft gerade.\nDer TVS-EPG Zwischenspeicher (=Cache) kann daher im Moment nicht gelöscht werden.")
			else:
				msgtext = "\nTVS-EPG Zwischenspeicher (=Cache) löschen?\n\nDies kann bei Problemen hilfreich sein,\ndanach sollte ein TVS-EPG Datenupdate durchgefürt werden."
				self.session.openWithCallback(self.return_ok3, MessageBox, msgtext, MessageBox.TYPE_YESNO, timeout=10, default=False)

	def return_ok1(self, answer):
		if answer:  # close plugin (e.g. after zap)
			self.exit()
		else:
			self.key_blue()
			self.show_tvtipsbox()

	def return_ok2(self, answer):
		if answer:
			if answer[1] == 1:
				callInThread(self.update_future_epg, force_refresh=False)
			elif answer[1] == 2:
				callInThread(self.update_future_epg, today_only=True)
			elif answer[1] == 3:
				callInThread(self.update_future_epg)
		self.show_tvtipsbox()

	def return_ok3(self, answer):
		if answer is True:
			self.remove_cache_paths()
			self.create_cache_paths()
			self.tvinfobox.show_dialog("TVS-EPG Zwischenspeicher (=Cache) erfolgreich gelöscht")

	def return_ok4(self, asset_url):
		callFromThread(self.return_ok4_ui, asset_url)

	def return_ok4_ui(self, asset_url):
		if asset_url:
			self.session.openWithCallback(self.return_ok1, TVFullscreen, asset_url, self.curr_date_dt)
		else:
			s_ref = self.session.nav.getCurrentlyPlayingServiceReference().toString()
			s_name = ServiceReference(s_ref).getServiceName()
			self.tvinfobox.show_dialog(f"Sender '{s_name}' wird vom TV Spielfilm Server nicht unterstützt.")

	def key_red(self):
		if not self.webaccess_ready():
			return
		if TVS_UPDATEACTIVE or tvglobals.AUTOUPDATEACTIVE:
			self.tvinfobox.show_dialog("Das TVS-EPG Datenupdate oder Autoupdate läuft gerade.\nDer TVS Import kann daher im Moment nicht durchgeführt werden.")
		else:
			self.hide_tvtipsbox()
			msgtext = "Importiere TV Spielfilm Sender?\nACHTUNG: Der TVS-EPG Zwischenspeicher (=Cache)\nmuß hierfür unwiderruflich gelöscht werden.\nDanach sollte ein TVS-EPG Datenupdate erfolgen.\n\nSind Sie sicher das Sie das wollen?"
			self.session.openWithCallback(self.return_red, MessageBox, msgtext, MessageBox.TYPE_YESNO, timeout=10, default=False)

	def return_red(self, answer):
		if answer is True:
			self.session.openWithCallback(self.import_file_cb, TVImport)
		else:
			self.show_tvtipsbox()

	def import_file_cb(self):
		self.remove_cache_paths()
		self.create_cache_paths()
		self.show_tvtipsbox()
		self.build_main_menu()

	def key_green(self):
		if not self.webaccess_ready():
			return
		if self.tvtipsbox.get_is_visible() and self.curr_asset_url:
			self.tvupdate.hide_dialog()
			self.hide_tvtipsbox()
			self.session.openWithCallback(self.return_ok1, TVFullscreen, self.curr_asset_url, self.curr_date_dt)
		else:
			self.show_tvtipsbox()

	def key_yellow(self):
		if TVS_UPDATEACTIVE or tvglobals.AUTOUPDATEACTIVE:
			self.session.openWithCallback(self.return_yellow, MessageBox, '\nTVS-EPG Daten Update abbrechen?', MessageBox.TYPE_YESNO, timeout=10, default=False)

	def return_yellow(self, answer):
		global TVS_UPDATESTOP
		if answer is True and (TVS_UPDATEACTIVE or tvglobals.AUTOUPDATEACTIVE):
			TVS_UPDATESTOP = True
			setattr(tvglobals, "AUTOUPDATESTOP", True)

	def key_blue(self):
		if self.tvupdate.get_is_visible():
			self.tvupdate.hide_dialog()
		elif TVS_UPDATEACTIVE:
			self.tvupdate.show_dialog()

	def down(self):
		self["mainmenu"].down()

	def up(self):
		self["mainmenu"].up()

	def left_up(self):
		self["mainmenu"].pageUp()

	def right_down(self):
		self["mainmenu"].pageDown()

	def show_tvtipsbox(self, delay=5000, first_tip=False):  # special case: the first tip could only be displayed after opening a screen and then interfere
		if not first_tip:
			self.tips_allow = True
		showtips = config.plugins.tvspielfilm.showtips.value
		if self.tips_dicts and self.tips_allow and (showtips == 2 or (first_tip and showtips == 1)):  # show tips allways?
			self.tvtipsbox.show_dialog()
			self.tipsbox_timer.start(delay, False)
			self["key_green"].setText("Sendungsdetail")
			self.tips_slideshow()

	def hide_tvtipsbox(self):
		self.tips_allow = False
		if self.tips_dicts:
			self.tipsbox_timer.stop()
			self.tvtipsbox.hide_dialog()
			self["key_green"].setText("Tipps anzeigen")

	def tips_slideshow(self):
		self.show_tip()
		self.curr_tip_cnt = (self.curr_tip_cnt + 1) % len(self.tips_dicts)

	def force_next_tip(self):
		if self.tvtipsbox.get_is_visible():
			self.tipsbox_timer.stop()
			self.tipsbox_timer.start(5000, False)
			self.curr_tip_cnt = (self.curr_tip_cnt + 1) % len(self.tips_dicts)
			self.show_tip()
		else:
			self.right_down()

	def force_prev_tip(self):
		if self.tvtipsbox.get_is_visible():
			self.tipsbox_timer.stop()
			self.tipsbox_timer.start(5000, False)
			self.curr_tip_cnt = (self.curr_tip_cnt - 1) % len(self.tips_dicts)
			self.show_tip()
		else:
			self.left_up()

	def get_tips(self, force_refresh=False):
		tips_file = self.tips_filename()
		self.curr_tip_cnt = 0
		if isfile(tips_file) and not force_refresh:
			try:
				with open(tips_file) as file:
					alltips_dicts = load(file)
					self.create_tips_dicts(alltips_dicts)
			except OSError as err_msg:
				print(f"[{self.MODULE_NAME}] ERROR in class 'TVMain:get_tips': {err_msg}!")
				self.call_gui(self.session.open, MessageBox, f"Datensatz 'Tipps' konnte nicht geladen:\n{err_msg}", type=MessageBox.TYPE_ERROR, timeout=10, close_on_any_key=True)
		else:
			callInThread(tvsptips.parse_tips, callback=self.get_tips_return, passthrough=tips_file)

	def get_tips_return(self, alltips_dicts, tipsfile):
		try:
			with open(tipsfile, "w") as file:
				dump(alltips_dicts, file)
		except OSError as err_msg:
			print(f"[{self.MODULE_NAME}] ERROR in class 'TVMain:get_tips_return': {err_msg}!")
			self.call_gui(self.session.open, MessageBox, f"Datensatz 'Tipps' konnte nicht gespeichert werden:\n{err_msg}", type=MessageBox.TYPE_ERROR, timeout=10, close_on_any_key=True)
		self.create_tips_dicts(alltips_dicts)

	def create_tips_dicts(self, alltips_dicts):
		self.call_gui(self.create_tips_dicts_ui, alltips_dicts)

	def create_tips_dicts_ui(self, alltips_dicts):
		self.create_tips_active = True
		print(f"[{self.MODULE_NAME}] Tips download starts.")
		tips_dicts = []
		import_dict = self.IMPORTDICT.keys()
		index = 0
		for tip_dict in alltips_dicts:
			channel_id = tip_dict.get("channel_id", "").lower()
			if channel_id in import_dict:  # channel was imported?
				self.curr_asset_url = tip_dict.get("asset_url", "")
				img_url = tip_dict.get("img_url", "").partition('"')[0]
				if img_url:
					img_file = self.image_filename(img_url)
					if img_file and not isfile(img_file):
						if index:  # download & save all further images only
							callInThread(self.image_download, img_url)
						else:  # download, save & set very first image immediately (if this tip_url is still up to date by then)
							callInThread(self.image_download, img_url, callback=self.set_tip_image, asset_url=self.curr_asset_url)
				title = tip_dict.get("title", "")
				timeinfos = tip_dict.get("time_infos", "")
				genre = tip_dict.get("genre", "")  # e.g. 'Katastrophenaction'
				channel_name = tip_dict.get("channel_nname", "") if config.plugins.tvspielfilm.channel_name.value else self.IMPORTDICT.get(channel_id, ["", "{unbekannt}"])[1]
				thumb_id_numeric = tip_dict.get("thumb_id_numeric", 0)
				is_tip = tip_dict.get("is_tip", False)
				is_top = tip_dict.get("is_top", False)
				is_new = tip_dict.get("is_new", False)
				is_live = tip_dict.get("is_live", False)
				first_year = tip_dict.get("first_year", "")
				country = tip_dict.get("country", "")
				country_year = tip_dict.get("country_year", "")
				category = tip_dict.get("category", "")  # e.g. 'SP' for 'Spielfilm'
				imdb_rating = tip_dict.get("imdb_rating", "")
				imdb_rating = f"IMDB-Wertung: {imdb_rating}" if imdb_rating else ""
				fsk = tip_dict.get("fsk", "")
				fsk_text = f"ab {fsk} Jahren" if fsk and fsk > -1 else ""
				meta_info = tip_dict.get("meta_info", {})
				conclusion = meta_info.get("conclusion", "")
				timestart_ts = tip_dict.get("time_start", "")
				tips_dicts.append({"title": title, "time_infos": timeinfos, "genre": genre, "category": category, "channel_name": channel_name,
								"country_year": country_year, "imdb_rating": imdb_rating, "fsk_text": fsk_text, "conclusion": conclusion, "is_top": is_top,
								"is_tip": is_tip, "is_new": is_new, "is_live": is_live, "fsk": fsk, "thumb_id_numeric": thumb_id_numeric, "img_url": img_url,
								"channel_id": channel_id, "asset_url": self.curr_asset_url, "first_year": first_year, "country": country, "time_start": timestart_ts})
				index += 1
		self.tips_dicts = tips_dicts
		print(f"[{self.MODULE_NAME}] Tips download was regularly terminated.")
		self.create_tips_active = False
		self.show_tvtipsbox(first_tip=self.curr_tip_cnt == 0)

	def set_tip_image(self, img_file, tip_url, column=0):  # column is required due to compatibility
		if isfile(img_file) and tip_url == self.curr_asset_url:  # show if current tip is still displayed
			self.tvtipsbox.set_widget_image("image", img_file)
			self.tvtipsbox.show_widget("image")

	def config(self):
		self.hide_tvtipsbox()
		self.key_blue()
		self.old_channel_name = config.plugins.tvspielfilm.channel_name.value
		self.session.openWithCallback(self.config_cb, TVSetup)

	def config_cb(self):
		if self.old_channel_name != config.plugins.tvspielfilm.channel_name.value:
			callInThread(self.get_tips, force_refresh=True)
		else:
			self.show_tvtipsbox()
		self.key_blue()
		self.build_main_menu()

	def exit(self):
		if TVS_UPDATEACTIVE or tvglobals.AUTOUPDATEACTIVE:
			self.session.openWithCallback(self.return_exit, MessageBox, '\nDas TVS-EPG Datenupdate läuft gerade!\nTVS-EPG Datenupdate abbrechen und Plugin verlassen?', MessageBox.TYPE_YESNO, timeout=10, default=False)
		else:
			self.return_exit(True)

	def return_exit(self, answer):
		global TVS_UPDATESTOP, TVS_UPDATEACTIVE
		if answer is True:
			self.webaccess_check_abandoned = True
			TVS_UPDATESTOP, TVS_UPDATEACTIVE = True, False
			setattr(tvglobals, "AUTOUPDATESTOP", True)
			self.session.deleteDialog(self.tvupdate)
			self.session.deleteDialog(self.tvinfobox)
			self.session.deleteDialog(self.tvtipsbox)
			self.close()

	def show_tip(self):
		tip_dict = self.tips_dicts[self.curr_tip_cnt]
		headline = f"Tipp des Tages ({self.curr_tip_cnt + 1}/{len(self.tips_dicts)})"
		for text, widget in [(headline, "headline"), (tip_dict.get("title", ""), "title"), (tip_dict.get("time_infos", ""), "time_infos"),
							(tip_dict.get("genre", ""), "genre"), (tip_dict.get("channel_name", ""), "channel_name"),
							(tip_dict.get("imdb_rating", ""), "imdb_rating"), (tip_dict.get("category", ""), "category"),
							(tip_dict.get("imdb_rating", ""), "imdb_rating"), (tip_dict.get("conclusion", ""), "conclusion")]:
			self.tvtipsbox.set_text(widget, text)
		for widget in ["is_top", "is_tip", "is_new", "is_live"]:
			if tip_dict.get(widget, ""):
				self.tvtipsbox.show_widget(widget)
			else:
				self.tvtipsbox.hide_widget(widget)
		iconpath = self.ICONPATH
		fsk = tip_dict.get("fsk", "")
		fskfile = join(iconpath, f"FSK_{fsk}.png") if fsk and fsk > -1 else ""
		if fskfile:
			self.tvtipsbox.set_widget_image("fsk", fskfile)
			self.tvtipsbox.show_widget("fsk")
		else:
			self.tvtipsbox.hide_widget("fsk")
		if thumb_id_numeric := tip_dict.get("thumb_id_numeric", 0):
			thumbfile = join(iconpath, f"thumb{thumb_id_numeric}.png")
			if isfile(thumbfile):
				self.tvtipsbox.set_widget_image("thumb", thumbfile)
				self.tvtipsbox.show_widget("thumb")
			else:
				self.tvtipsbox.hide_widget("thumb")
		img_url = tip_dict.get("img_url", "")
		self.curr_asset_url = tip_dict.get("asset_url", "")
		img_file = self.image_filename(img_url)
		if isfile(img_file):
			self.tvtipsbox.set_widget_image("image", img_file)
			self.tvtipsbox.show_widget("image")
		elif self.create_tips_active:  # tips creation still in progress?
				self.tvtipsbox.hide_widget("image")
		else:  # download, save & set very first image immediately (if this tip_url is still up to date by then)
			callInThread(self.image_download, img_url, callback=self.set_tip_image, asset_url=self.curr_asset_url)
		picon_file = self.get_picon_file(tip_dict.get("channel_id", ""))
		if picon_file and isfile(picon_file):
			self.tvtipsbox.set_widget_image("picon", picon_file)
			self.tvtipsbox.show_widget("picon")
		else:
			self.tvtipsbox.hide_widget("picon")
		sref = self.IMPORTDICT.get(tip_dict.get("channel_id", ""), ["", ""])[0]
		start_ts, end_ts = tip_dict.get("time_start", ""), 0
		has_timer = self.is_already_listed((start_ts, end_ts), sref) if start_ts and sref else False
		if has_timer:
			self.tvtipsbox.show_widget("has_timer")
		else:
			self.tvtipsbox.hide_widget("has_timer")

	def set_progress_range(self, index, range):
		self.call_gui(self.set_progress_range_ui, index, range)

	def set_progress_range_ui(self, index, range):
		self.tvupdate.set_range(f"progressbar{index}", range)

	def set_progress_values(self, index, valuelist):
		self.call_gui(self.set_progress_values_ui, index, valuelist)

	def set_progress_values_ui(self, index, valuelist):
		self.tvupdate.set_text(f"progress_hdr{index}", valuelist[0])
		self.tvupdate.set_value(f"progressbar{index}", valuelist[1])
		self.tvupdate.set_text(f"progress_txt{index}", valuelist[2])

	def update_future_epg(self, force_refresh=True, today_only=False):
		global TVS_UPDATEACTIVE, TVS_UPDATESTOP
		if TVS_UPDATEACTIVE or tvglobals.AUTOUPDATEACTIVE:
			return False
		TVS_UPDATEACTIVE, TVS_UPDATESTOP = True, False
		self.call_gui(self.tvupdate.show_dialog)
		succeeded = False
		try:
			from .tvautoupdate import tvautoupdate
			succeeded = tvautoupdate.update_future_epg(gui_output=self, force_refresh=force_refresh, today_only=today_only)
		finally:
			self.call_gui(self.tvupdate.hide_dialog)
			self.call_gui(self.tvinfobox.show_dialog, "TVS-EPG Datenupdate erfolgreich beendet." if succeeded else "TVS-EPG Datenupdate erfolgreich abgebrochen.")
			TVS_UPDATEACTIVE, TVS_UPDATESTOP = False, False
		return succeeded


class TVSelectChannelCategory(TVScreenHelper, Screen):
	skin = """
	<screen name="TVSelectChannelCategory" position="480,50" size="320,620" backgroundColor="#16000000" flags="wfNoBorder" resolution="1280,720" title="TV Spielfilm Servicedatei">
		<eLabel position="0,0" size="320,620" backgroundColor="#00203060" zPosition="-2" />
		<eLabel position="2,2" size="316,616" zPosition="-1" />
		<eLabel position="2,2" size="316,58" backgroundColor=" black,#00203060,horizontal" zPosition="1" />
		<eLabel position="2,60" size="316,2" backgroundColor="#0027153c,#00101093,black,horizontal" zPosition="10" />
		<ePixmap position="0,0" size="220,60" pixmap="{tvs_path}/pics/{res}/logos/TVSpielfilm.png" alphatest="blend" zPosition="13" />
		<widget source="release" render="Label" position="180,28" size="80,20" font="Regular;18" textBorderColor="#00505050" textBorderWidth="1" foregroundColor="#00ffff00" backgroundColor="#16000000" valign="center" zPosition="12" transparent="1" />
		<widget source="menulist" render="Listbox" position="2,60" size="316,560" itemCornerRadiusSelected="4" itemGradientSelected="#051a264d,#10304070,#051a264d,horizontal" enableWrapAround="1" foregroundColorSelected="white" backgroundColor="#16000000" transparent="1" scrollbarMode="showOnDemand">
			<convert type="TemplatedMultiContent">{"template": [
				MultiContentEntryText(pos=(0,0), size=(316,40), font=0,  flags=RT_HALIGN_CENTER|RT_VALIGN_CENTER, text=0)  # menutext
				],
				"fonts": [gFont("Regular",24)],
				"itemHeight":40
				}
			</convert>
		</widget>
	</screen>
	"""

	def __init__(self, session):
		self.session = session
		self.skin = self.skin.replace("{tvs_path}", tvglobals.PLUGINPATH).replace("{res}", tvglobals.RESOLUTION)
		Screen.__init__(self, session)
		self.channels, self.categories, self.channel_dicts = [], [], []
		self.category_index, self.channel_index = 0, 0
		self.category_menu = True
		self.channel_loaded = False
		self.IMPORTDICT = self.read_imported_file()
		self["release"] = StaticText(self.RELEASE)
		self["menulist"] = List()
		self["actions"] = ActionMap(["OkCancelActions"], {
			"ok": self.key_ok,
			"cancel": self.key_exit
		}, -1)
		callInThread(self.create_channeldicts)
		self.onLayoutFinish.append(self.refresh_menu)

	def refresh_menu(self):
		self["menulist"].updateList(self.get_categories())

	def create_channeldicts(self):
		channel_dicts = []
		for channel_dict in tvspchannels.parse_channels():  # add channel_names to dict
			channel_dict["channel_name"] = self.IMPORTDICT.get(channel_dict["channel_id"], ["", "{unbekannt}"])[1]
			channel_dicts.append(channel_dict)
		self.channel_dicts = channel_dicts
		self.channel_loaded = True
		callFromThread(self.refresh_menu)

	def get_categories(self):
		used_channels = []
		categories = []
		for channel in self.channel_dicts:
			channel_id = channel.get("channel_id", "").lower()
			if channel_id in self.IMPORTDICT.keys():  # channel was imported?
				category = channel.get("category", "")  # e.g. 'SP' for 'Spielfilm'
				if category and category not in categories:  # found category already listed?
					categories.append(category)
				used_channels.append({"channel_id": channel_id, "category": category})
		self.categories = categories
		return categories  # e.g. ['Hauptsender', 'Auslandssender', 'Spartensender', 'News und Dokus', 'Dritte Programme', 'Sportsender', 'Kindersender', 'Musiksender', 'Shopping', 'Regionalsender', 'Sky Cinema', 'Pay TV', 'Sky Sport', 'Sky Entertainment']

	def key_exit(self):
		if self.category_menu:
			self.close(False)
		else:
			self.category_menu = True
			self["menulist"].updateList(self.categories)
			self["menulist"].setCurrentIndex(self.category_index)

	def show_category_channels(self):
		category = self["menulist"].getCurrent()
		self.category_index = self["menulist"].getCurrentIndex()
		channels = []
		import_dict = self.IMPORTDICT.keys()
		for channel_dict in self.channel_dicts:
			channel_id = channel_dict.get("channel_id", "").lower()
			if channel_dict.get("category", "") == category and channel_id in import_dict:  # channel is in this category and was imported?
				channel_name = channel_dict.get("channel_name", "") if config.plugins.tvspielfilm.channel_name.value else self.IMPORTDICT.get(channel_id, ["", ""])[1]
				channels.append((channel_name, channel_id))
		self.channels = channels
		self["menulist"].updateList(channels)
		self["menulist"].setCurrentIndex(self.channel_index)

	def key_ok(self):
		if not self["menulist"] or not self.channel_loaded:
			return
		if self.category_menu:
			self.category_menu = False
			self.show_category_channels()
		else:
			self.channel_index = self["menulist"].getCurrentIndex()
			if channel := self["menulist"].getCurrent():
				self.session.openWithCallback(self.key_ok_return, TVOverview, (("", "day"), 0), single_channel_id=channel[1])

	def key_ok_return(self, answer):
		if answer:
			self.close(True)


class TVImport(TVScreenHelper, Screen):
	skin = """
	<screen name="TVImport" position="480,90" size="360,550" backgroundColor="#16000000" flags="wfNoBorder" resolution="1280,720" title="TV Spielfilm Servicedatei">
		<eLabel position="0,0" size="360,500" backgroundColor="#00203060" zPosition="-2" />
		<eLabel position="2,2" size="356,496" zPosition="-1" />
		<eLabel position="2,2" size="356,58" backgroundColor=" black,#00203060,horizontal" zPosition="1" />
		<eLabel position="2,60" size="356,2" backgroundColor="#0027153c,#00101093,black,horizontal" zPosition="10" />
		<ePixmap position="0,0" size="220,60" pixmap="{tvs_path}/pics/{res}/logos/TVSpielfilm.png" alphatest="blend" zPosition="13" />
		<widget source="release" render="Label" position="180,28" size="80,20" font="Regular;18" textBorderColor="#00505050" textBorderWidth="1" foregroundColor="#00ffff00" backgroundColor="#16000000" valign="center" zPosition="12" transparent="1" />
		<widget source="bouquetslist" render="Listbox" position="2,60" size="356,440" itemCornerRadiusSelected="4" itemGradientSelected="#051a264d,#10304070,#051a264d,horizontal" enableWrapAround="1" foregroundColorSelected="white" backgroundColor="#16000000" transparent="1" scrollbarMode="showOnDemand">
			<convert type="TemplatedMultiContent">{"template": [
				MultiContentEntryText(pos=(0,0), size=(346,40), font=0,  flags=RT_HALIGN_CENTER|RT_VALIGN_CENTER, text=0)  # menutext
				],
				"fonts": [gFont("Regular",24)],
				"itemHeight":40
				}
			</convert>
		</widget>
		<eLabel name="button_blue" position="40,510" size="6,36" backgroundColor="#101093,#4040ff,vertical" zPosition="1" />
		<widget source="key_blue" render="Label" position="54,516" size="300,26" font="Regular;18" valign="center" halign="left" foregroundColor="grey" backgroundColor="#16000000" transparent="1" />
	</screen>
	"""

	def __init__(self, session):
		self.session = session
		self.skin = self.skin.replace("{tvs_path}", tvglobals.PLUGINPATH).replace("{res}", tvglobals.RESOLUTION)
		Screen.__init__(self, session)
		self.tvinfobox = session.instantiateDialog(TVInfoBox)
		self.maplist, self.totaldupes, self.totalimport = [], [], []
		self.totalsupp, self.totalunsupp = [], []
		self.mappinglog = ""
		self["release"] = StaticText(self.RELEASE)
		self["bouquetslist"] = List()
		self["key_blue"] = StaticText("Überprüfe Konvertierungsregeln")
		self['actions'] = ActionMap(["OkCancelActions", "ColorActions"], {
			"ok": self.key_ok,
			"cancel": self.key_exit,
			"blue": self.key_blue
			}, -1)
		if self.create_cache_paths():
			self.exit()
		if self.update_mappingfile():
			self.tvinfobox.show_dialog("Die Sender-Zuweisungstabelle\n'/etc/enigma2/tvspielfilm/tvs_mapping.txt'\nwurde aktualisiert.", 5000)
		self.maplist = self.read_mappinglist()
		self.onShown.append(self.shown_finished)

	def shown_finished(self):
		if isfile(self.MAPFILE):
			self.get_all_bouquets()
		else:
			print(f"[{self.MODULE_NAME}] Error in class 'TVImport:shown_finished': file '{self.MAPFILE}' not found.")
			self.session.open(MessageBox, f"Datei '{self.MAPFILE}' kann weder gefunden noch angelegt werden.\nTVS Import kann daher nicht fortgefahren werden!", MessageBox.TYPE_ERROR, timeout=5, close_on_any_key=True)
			self.key_exit()

	def key_exit(self):
		self.close()

	def key_blue(self):
		deferToThread(self.check_mapping_rules).addCallback(self.check_mapping_rules_done)  # keep the web request out of the GUI thread

	def check_mapping_rules_done(self, result):
		self.session.open(MessageBox, f"Konvertierungsregeln in der Datei:\n'{self.MAPFILE}'\nwurden geprüft.\n\nDie detaillierte Analyse finden Sie in der Logdatei:\n'{self.mappinglog}'", MessageBox.TYPE_INFO, timeout=10, close_on_any_key=True)

	def key_ok(self):
		current = self["bouquetslist"].getCurrent()  # e.g. ('Favoriten (TV)', <enigma.eServiceReference; proxy of <Swig Object of type 'eServiceReference *' at 0xa70d46f8> >)
		importlist, dupeslist, supplist, unsupplist = self.import_bouquet(current[1])
		if importlist:
			# combine two lists without duplicate entries while retaining the sort order
			self.totalimport = list(dict(dict(self.totalimport), **dict(importlist)).items())  # will be later reduced by TVChannelselection
			self.totaldupes = list(dict(dict(self.totaldupes), **dict(dupeslist)).items())
			self.totalsupp = list(dict(dict(self.totalsupp), **dict(supplist)).items())  # complete list of channels supported by the server
			self.totalunsupp = list(dict(dict(self.totalunsupp), **dict(unsupplist)).items())
			totalfound = importlist + dupeslist + unsupplist
			self.append_import_log(current[0], totalfound, importlist, dupeslist, unsupplist)
			msg = f"\nSoeben im Bouquet gefundene Kanäle: {len(totalfound)}"
			msg += f"\nSoeben erfolgreich importierte Kanäle: {len(importlist)}"
			msg += f"\nSoeben nicht importierte doppelte Kanäle: {len(dupeslist)}"
			msg += f"\nSoeben gefundene Kanäle die nicht von TVSpielfilm unterstützt werden: {len(unsupplist)}"
			msg += f"\n{'-' * 120}"
			msg += f"\nBisher erfolgreich importierte Kanäle: {len(self.totalimport)}"
			msg += f"\nBisher nicht importierte doppelte Kanäle: {len(self.totaldupes)}"
			msg += f"\nBisher gefundene Kanäle die nicht von TVSpielfilm unterstützt werden: {len(self.totalunsupp)}"
			msg += "\n\nSoll noch ein weiteres TV Bouquet importiert werden?"
		else:
			msg = "\nKeine TV Spielfilm Kanäle gefunden.\nBitte wähle ein anderes TV Bouquet."
		self.session.openWithCallback(self.another_bouquet, MessageBox, msg, MessageBox.TYPE_YESNO, timeout=30, default=False)

	def another_bouquet(self, answer):
		if answer is True:
			self.get_all_bouquets()
		else:  # create TVSpielfilm service- and dupesJSON and finish successfully
			self.session.openWithCallback(self.another_bouquet_cb, TVChannelselection, self.totalimport)

	def another_bouquet_cb(self, answer):
		def write_dict_file(file_name, data_list, err_text):
			try:
				with open(f"{file_name}.new", 'w') as file:
					dump(dict(data_list), file)
				rename(f"{file_name}.new", file_name)
			except OSError as err_msg:
				print(f"[{self.MODULE_NAME}] ERROR in class 'TVImport:another_bouquet_cb': {err_msg}!")
				self.session.open(MessageBox, f"{err_text}:\n{err_msg}", type=MessageBox.TYPE_ERROR, timeout=10, close_on_any_key=True)

		if answer:
			if answer[0] is True:
				if self.totalimport:
					imported_channels = []
					for index, channel in enumerate(answer[1]):
						if channel[1]:
							imported_channels.append((self.totalimport[index][1][0], (self.totalimport[index][0], self.totalimport[index][1][1])))  # e.g. ('ard', ('1:0:19:283D:41B:1:FFFF0000:0:0:0:', 'Das Erste HD'))
					write_dict_file(self.IMPORTFILE, imported_channels, "Fehler in 'weiteres Bouquet'")
				if self.totalsupp:
					supported_channels = []
					for index, channel in enumerate(self.totalsupp):
						if channel[1]:
							supported_channels.append((self.totalsupp[index][1][0], (self.totalsupp[index][0], self.totalsupp[index][1][1])))
					write_dict_file(self.SUPPFILE, supported_channels, "Fehler beim Einlesen der unterstützten Kanäle'")
				if self.totaldupes:  # all unused (duplicate) channels in bouquets
					write_dict_file(self.DUPESFILE, self.totaldupes, "Fehler beim Einlesen der doppelten Kanäle'")
				if self.totalunsupp:  # all channels not supported by server
					write_dict_file(self.UNSUPPFILE, self.totalunsupp, "Fehler beim Einlesen der nicht unterstützten Kanäle'")
				self.IMPORTDICT = self.read_imported_file()  # load imported channel data
				self.tvinfobox.show_dialog("Senderimport erfolgreich durchgeführt.")
			else:
				self.session.open(MessageBox, "Senderimport abgebrochen!", type=MessageBox.TYPE_WARNING, timeout=2, close_on_any_key=True)
				self.tvinfobox.show_dialog("Dieser Sender wird von TV Spielfilm nicht unterstützt.")
			self.close()

	def get_all_bouquets(self):
		bouquetstr = '1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "bouquets.tv" ORDER BY bouquet' if config.usage.multibouquet.value else '1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "userbouquet.favourites.tv" ORDER BY bouquet'
		root = eServiceReference(bouquetstr)
		service_handler = eServiceCenter.getInstance()
		bouquets_list = []
		if config.usage.multibouquet.value:
			slist = service_handler.list(root)
			while True:
				service = slist.getNext()
				if not service.valid():
					del slist
					break
				if service.flags & eServiceReference.isDirectory:
					if info := service_handler.info(service):
						bouquets_list.append((info.getName(service), service))
		elif info := service_handler.info(root):
			bouquets_list.append((info.getName(root), root))
		self["bouquetslist"].updateList(bouquets_list)

	def import_bouquet(self, bouquet=None):
		if not bouquet:  # fallback to favorites
			bouquet = eServiceReference('1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "userbouquet.favourites.tv" ORDER BY bouquet')
		supported, unsupported, importlist, dupeslist, = [], [], [], []
		slist = ServiceList(bouquet, validate_commands=False)
		services = slist.getServicesAsList(format='SN')  # z.B. [('1:0:27:212F:31B:1:FFFF0000:0:0:0:', 'Das Erste HD'), ...]
		for service in services:
			sname = service[1].strip()
			sref = f"{service[0].split('http')[0]}{{IPTV-Stream}}" if "http" in service[0].lower() else service[0]
			if found := next((channel_id for channel_id, regstr in self.maplist if match(compile(regstr), sname.lower())), ""):
				supported.append((sref.rstrip(), (found.rstrip(), sname.rstrip())))
			else:
				unsupported.append((sref.rstrip(), ("", sname.rstrip())))
		for item in supported:  # divide into import and duplicate
			if item[1][0] not in [x[1][0] for x in importlist]:
				importlist.append(item)
			else:
				dupeslist.append(item)
		return importlist, dupeslist, supported, unsupported

	def read_mappinglist(self):  # Read mapping (=translation rules 'TVSpielfilm channel abbreviation: E2 service name')
		line = "{No line evaluated yet}"
		maplist = []
		try:
			with open(self.MAPFILE) as file:  # /etc/enigma2/TVSpielfilm
				for line in file.read().replace(",", "").strip().split("\n"):
					if not line.startswith("#"):
						if items := line.strip().split(": "):
							maplist.append((items[0], items[1]))
		except Exception as err_msg:
			print(f"[{self.MODULE_NAME}] Exception error class 'TVImport:read_mappinglist' in {line}: {err_msg}")
			self.session.open(MessageBox, f"Fehler beim Einlesen der Sender-Zuweisungstabelle 'tvs_mapping.txt':\n{err_msg}", type=MessageBox.TYPE_ERROR, timeout=10, close_on_any_key=True)
		return maplist

	def append_import_log(self, bouquetname, totalfound, importlist, dupeslist, unsupported):  # append last successful import to logfile
		try:
			with open(join(self.LOGPATH, "bouquetimport.log"), "a") as file:
				file.write(f"{'=' * 78}\n{len(totalfound)} Kanäle im Bouquet gefunden '{bouquetname}' (inkl. doppelter TVSpielfilm-Kürzel)\n{'=' * 78}\n")
				formatstr = "{0:<10} {1:<40} {2:<0}\n"
				for item in totalfound:
					file.write(formatstr.format(*(item[1][0] or "n/a", item[0], item[1][1])))
				file.write(f"\n{len(importlist)} importierte TVSpielfilm Kanäle (ohne doppelte TVSpielfilm-Verknüpfungen):\n{'-' * 78}\n")
				for item in importlist:
					file.write(formatstr.format(*(item[1][0], item[0], item[1][1])))
				file.write(f"\n{len(dupeslist)} nicht importierte Kanäle (weil doppelte TVSpielfilm-Verknüpfungen):\n{'-' * 78}\n")
				for item in dupeslist:
					file.write(formatstr.format(*(item[1][0], item[0], item[1][1])))
				file.write(f"\n{len(unsupported)} Kanäle, die von TV-Spielfilm nicht unterstützt werden:\n{'-' * 78}\n")
				for item in unsupported:
					file.write(formatstr.format(*("n/a", item[0], item[1][1])))
				file.write("\n")
		except OSError as err_msg:
			print(f"[{self.MODULE_NAME}] ERROR in class 'TVImport:append_import_log': {err_msg}!")
			self.session.open(MessageBox, f"Fehler beim Erstellen des Protokolls 'bouquetimport.log':\n{err_msg}", type=MessageBox.TYPE_ERROR, timeout=10, close_on_any_key=True)

	def check_mapping_rules(self):  # tool: checks whether conversion rules are missing / outdated / double in the mapping file
		map_list = sorted(self.maplist, key=lambda x: x[0])
		map_keys = [x[0] for x in map_list]
		channel_dicts = tvspchannels.parse_channels()
		channel_keys = [x.get("channel_id", "n/a").lower() for x in channel_dicts]
		used_channels = []
		import_dict = self.IMPORTDICT.keys()
		for channel in channel_dicts or []:
			channel_id = channel.get("channel_id", "").lower()
			if channel_id in import_dict:  # channel was imported?
				category = channel.get("category", "")  # e.g. 'SP' for 'Spielfilm'
				name = self.IMPORTDICT.get(channel_id, ["", ""])[1]
				used_channels.append({"channel_id": channel_id, "name": name, "category": category})
		if used_channels:
			tabpos = "{0:<10} {1:<0}\n"
			self.mappinglog = join(self.LOGPATH, "mappingrules.log")
			try:
				with open(self.mappinglog, "w") as file:
					file.write(f"{len(used_channels)} Kanäle gefunden, die von TV Spielfilm unterstützt werden\n")
					file.write("\nFehlende Regel(n) für Kanäle, die von TV Spielfilm unterstützt werden: ")
					notfound = []
					for service in used_channels:  # search for missing conversion rules
						shortkey = service.get("channel_id", "n/a").lower()
						if shortkey not in map_keys:
							notfound.append((shortkey, service.get("name", "n/v")))
					if notfound:
						file.write(f"\n{tabpos.format(*('Kürzel', 'Sendername'))}")
						file.write(f"{'-' * 58}\n")
						for service in notfound:
							file.write(tabpos.format(*service))
						file.write("EMPFEHLUNG: Diese Regel(n) in die Datei 'tvs_mapping.txt' einpflegen.\n")
					else:
						file.write("\n{Keine fehlenden Regel(n) gefunden}\n")
					file.write("\nVeraltete Regel(n) für Kanäle, die von TV Spielfilm nicht unterstützt werden: ")
					outdated = []
					outdated.extend((service[0], service[1]) for service in map_list if service[0] not in channel_keys)
					if outdated:
						file.write(f"\n{tabpos.format(*('Kürzel', 'Umsetzungsregel'))}")
						file.write(f"{'-' * 58}\n")
						for service in outdated:
							file.write(tabpos.format(*service))
						file.write("EMPFEHLUNG: Diese Regel(n) aus der Datei 'tvs_mapping.txt' entfernen.\n")
					else:
						file.write("\n{Keine veraltete Regel(n) gefunden}\n")
					file.write("\nDoppelte Regel(n) für Kanäle, die von TV Spielfilm unterstützt werden: ")
					double = []
					double.extend((map_list[index][0], map_list[index][1]) for index, key in enumerate(map_keys) if map_keys.count(key) > 1)
					if double:
						file.write(f"\n{tabpos.format(*('Kürzel', 'Umsetzungsregel'))}")
						file.write(f"{'-' * 58}\n")
						for service in double:
							file.write(tabpos.format(*service))
						file.write("EMPFEHLUNG: Im Zweifel in der Datei 'tvs_mapping.txt' belasssen!\nSender könnten z.B. bei verschiedenen Anbietern unter verschiedenen Namen geführt werden.\n")
					else:
						file.write("\n{Keine doppelten Regel(n) gefunden}\n")
			except OSError as err_msg:
				print(f"[{self.MODULE_NAME}] ERROR in class 'TVImport:check_mapping_rules': {err_msg}!")
				self.session.open(MessageBox, f"Fehler beim Prüfen Protokolls 'bouquetimport.log':\n{err_msg}" % err_msg, type=MessageBox.TYPE_ERROR, timeout=10, close_on_any_key=True)


class TVChannelselection(Screen, tvglobals):
	skin = """
	<screen name="TVChannelselection" position="480,20" size="320,660" backgroundColor="#16000000" flags="wfNoBorder" resolution="1280,720" title="TV Spielfilm Kanalauswahl">
		<eLabel position="0,0" size="320,660" backgroundColor="#00203060" zPosition="-2" />
		<eLabel position="2,2" size="316,656" zPosition="-1" />
		<eLabel position="2,2" size="316,58" backgroundColor=" black,#00203060,horizontal" zPosition="1" />
		<eLabel position="2,60" size="316,2" backgroundColor="#0027153c,#00101093,black,horizontal" zPosition="10" />
		<eLabel position="2,616" size="316,2" backgroundColor="#0027153c,#00101093,black,horizontal" zPosition="10" />
		<ePixmap position="0,0" size="220,60" pixmap="{tvs_path}/pics/{res}/logos/TVSpielfilm.png" alphatest="blend" zPosition="13" />
		<widget source="release" render="Label" position="180,28" size="80,20" font="Regular;18" textBorderColor="#00505050" textBorderWidth="1" foregroundColor="#00ffff00" backgroundColor="#16000000" valign="center" zPosition="12" transparent="1" />
		<widget source="channel_list" render="Listbox" position="2,62" size="316,550" itemCornerRadiusSelected="4" itemGradientSelected="#051a264d,#10304070,#051a264d,horizontal" enableWrapAround="1" foregroundColorSelected="white" backgroundColor="#16000000" transparent="1" scrollbarMode="showOnDemand" scrollbarBorderWidth="1" scrollbarWidth="10" scrollbarBorderColor="blue" scrollbarForegroundColor="#00203060">
			<convert type="TemplatedMultiContent">{"template": [
				MultiContentEntryText(pos=(5,2), size=(270,30), font=0, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=0),  # menutext
				MultiContentEntryPixmapAlphaBlend(pos=(280,8), size=(20,20), flags=BT_SCALE, png="{tvs_path}/pics/{res}/icons/checkbox.png"),  # checkbox
				MultiContentEntryText(pos=(282,6), size=(18,18), font=1, color=MultiContentTemplateColor(2), color_sel=MultiContentTemplateColor(2), flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=1)  # checkmark
				],
				"fonts": [gFont("Regular",20),gFont("Regular",20),gFont("Regular",36)],
				"itemHeight":34
				}
			</convert>
		</widget>
		<eLabel name="button_red" position="10,626" size="6,30" backgroundColor="#00821c17,#00fe0000,vertical" zPosition="1" />
		<eLabel name="button_green" position="180,626" size="6,30" backgroundColor="#00006600,#0024a424,vertical" zPosition="1" />
		<widget source="key_red" render="Label" position="24,628" size="150,30" font="Regular;18" foregroundColor="#00ffffff" backgroundColor="#00000000" transparent="1" zPosition="2" halign="left" valign="center" />
		<widget source="key_green" render="Label" position="194,628" size="150,30" font="Regular;18" foregroundColor="#00ffffff" backgroundColor="#00000000" transparent="1" zPosition="1" halign="left" valign="center" />
	</screen>
	"""

	def __init__(self, session, totalimport):
		self.totalimport = totalimport
		self.skin = self.skin.replace("{tvs_path}", tvglobals.PLUGINPATH).replace("{res}", tvglobals.RESOLUTION)
		Screen.__init__(self, session)
		self.tvinfobox = session.instantiateDialog(TVInfoBox)
		self.channellist = []
		self.deselect = True
		self["release"] = StaticText(tvglobals.RELEASE)
		self["channel_list"] = List()
		self["key_red"] = StaticText("Alle abwählen")
		self["key_green"] = StaticText("Übernehmen")
		self['actions'] = ActionMap(["OkCancelActions", "ColorActions"], {
			"ok": self.key_ok,
			"cancel": self.key_exit,
			"red": self.key_red,
			"green": self.key_green
		}, -1)
		self.onShown.append(self.shown_finished)

	def shown_finished(self):
		for service in self.totalimport:
			self.channellist.append([service[1][1], True])
		self.update_channellist()

	def update_channellist(self):
		skin_list = [(channel[0], "✔" if channel[1] else "✘", int("0x0004c81b", 0) if channel[1] else int("0x00f50808", 0)) for channel in self.channellist]  # alternatively "✓", "✗"
		self["channel_list"].updateList(skin_list)

	def key_ok(self):
		curr_index = self["channel_list"].getCurrentIndex()
		if self.channellist:
			self.channellist[curr_index][1] = not self.channellist[curr_index][1]
		self.update_channellist()

	def key_red(self):
		if self.channellist:
			if self.deselect:
				for index in range(len(self.channellist)):
					self.channellist[index][1] = False
				self["key_red"].setText("Alle auswählen")
			else:
				for index in range(len(self.channellist)):
					self.channellist[index][1] = True
				self["key_red"].setText("Alle abwählen")
		self.deselect = not self.deselect
		self.update_channellist()

	def key_green(self):
		if self.channellist:
			if all(not x[1] for x in self.channellist):
				self.tvinfobox.show_dialog("Bitte wählen Sie mindestens einen Sender aus!")
				self.update_channellist()
			else:
				self.close((True, self.channellist))

	def key_exit(self):
		self.close((False, []))


class TVSetup(TVScreenHelper, Setup):
	def __init__(self, session):
		Setup.__init__(self, session, "TVSetup", plugin="Extensions/TVSpielfilm", PluginLanguageDomain="TVSpielfilm")
		self["key_blue"] = StaticText("Filtereinstellungen")
		self["entry_actions"] = HelpableActionMap(self, ["ColorActions"], {
			"blue": (self.key_blue, "Filtereinstellungen")
		}, prio=0, description="TVSpielfilm Filtereinstellungen")

	def key_blue(self):
		self.session.open(TVFilterselection)

	def key_select(self):
		if self.getCurrentItem() == config.plugins.tvspielfilm.cache_path:
			self.session.openWithCallback(self.key_select_cb1, TVSettingsLocationBox, curr_dir=config.plugins.tvspielfilm.cache_path.value)
			return
		if self.getCurrentItem() == config.plugins.tvspielfilm.piconpath:
			self.session.openWithCallback(self.key_select_cb1, TVSettingsLocationBox, curr_dir=config.plugins.tvspielfilm.piconpath.value)
			return
		Setup.key_select(self)

	def key_select_cb1(self, path):
		if path is not None:
			path = join(path, "")
			config.plugins.tvspielfilm.cache_path.value = path
		self["config"].invalidateCurrent()
		self.changedEntry()

	def key_select_cb2(self, path):
		if path is not None:
			path = join(path, "")
			config.plugins.tvspielfilm.piconpath.value = path
		self["config"].invalidateCurrent()
		self.changedEntry()


class TVFilterselection(Screen):
	skin = """
	<screen name="TVFilterselection" position="400,20" size="480,660" backgroundColor="#16000000" flags="wfNoBorder" resolution="1280,720" title="TV Spielfilm Filterauswahl">
		<eLabel position="0,0" size="480,660" backgroundColor="#00203060" zPosition="-2" />
		<eLabel position="2,2" size="476,656" zPosition="-1" />
		<eLabel position="2,2" size="476,58" backgroundColor=" black,#00203060,horizontal" zPosition="1" />
		<eLabel position="2,60" size="476,2" backgroundColor="#0027153c,#00101093,black,horizontal" zPosition="10" />
		<eLabel position="2,616" size="476,2" backgroundColor="#0027153c,#00101093,black,horizontal" zPosition="10" />
		<ePixmap position="0,0" size="220,60" pixmap="{tvs_path}/pics/{res}/logos/TVSpielfilm.png" alphatest="blend" zPosition="13" />
		<widget source="release" render="Label" position="180,28" size="80,20" font="Regular;18" textBorderColor="#00505050" textBorderWidth="1" foregroundColor="#00ffff00" backgroundColor="#16000000" valign="center" zPosition="12" transparent="1" />
		<eLabel text="Filter aktivieren / deaktivieren" position="2,62" size="476,40" font="Regular;28" zPosition="10" />
		<widget source="filter_list" render="Listbox" position="2,102" size="476,550" itemCornerRadiusSelected="4" itemGradientSelected="#051a264d,#10304070,#051a264d,horizontal" enableWrapAround="1" foregroundColorSelected="white" backgroundColor="#16000000" transparent="1" scrollbarMode="showOnDemand" scrollbarBorderWidth="1" scrollbarWidth="10" scrollbarBorderColor="blue" scrollbarForegroundColor="#00203060">
			<convert type="TemplatedMultiContent">{"template": [
				MultiContentEntryText(pos=(5,2), size=(270,30), font=0, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=0),  # menutext
				MultiContentEntryText(pos=(310,2), size=(120,30), font=0, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=1),  # startfilter
				MultiContentEntryPixmapAlphaBlend(pos=(440,8), size=(20,20), flags=BT_SCALE, png="{tvs_path}/pics/{res}/icons/checkbox.png"),  # checkbox
				MultiContentEntryText(pos=(442,6), size=(18,18), font=1, color=MultiContentTemplateColor(3), color_sel=MultiContentTemplateColor(3), flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=2)  # checkmark
				],
				"fonts": [gFont("Regular",20),gFont("Regular",20),gFont("Regular",36)],
				"itemHeight":34
				}
			</convert>
		</widget>
		<eLabel name="button_red" position="10,626" size="6,30" backgroundColor="#00821c17,#00fe0000,vertical" zPosition="1" />
		<eLabel name="button_green" position="160,626" size="6,30" backgroundColor="#00006600,#0024a424,vertical" zPosition="1" />
		<eLabel name="button_yellow" position="310,626" size="6,30" backgroundColor="#007a6213,#00e6c619,vertical" zPosition="1" />
		<widget source="key_red" render="Label" position="24,628" size="140,30" font="Regular;18" foregroundColor="#00ffffff" backgroundColor="#00000000" transparent="1" zPosition="2" halign="left" valign="center" />
		<widget source="key_green" render="Label" position="174,628" size="140,30" font="Regular;18" foregroundColor="#00ffffff" backgroundColor="#00000000" transparent="1" zPosition="1" halign="left" valign="center" />
		<widget source="key_yellow" render="Label" position="324,628" size="140,30" font="Regular;18" foregroundColor="#00ffffff" backgroundColor="#00000000" transparent="1" zPosition="1" halign="left" valign="center" />
	</screen>
	"""

	def __init__(self, session):
		self.skin = self.skin.replace("{tvs_path}", tvglobals.PLUGINPATH).replace("{res}", tvglobals.RESOLUTION)
		Screen.__init__(self, session)
		self.tvinfobox = session.instantiateDialog(TVInfoBox)
		self.defaultfilter = int(config.plugins.tvspielfilm.defaultfilter.value)
		self.filtersettings = loads(config.plugins.tvspielfilm.filtersettings.value)
		self.deselect = True
		self["release"] = StaticText(tvglobals.RELEASE)
		self["filter_list"] = List()
		self["key_red"] = StaticText("Alle abwählen")
		self["key_green"] = StaticText("Übernehmen")
		self["key_yellow"] = StaticText("Startfilter setzen")
		self['actions'] = ActionMap(["OkCancelActions", "ColorActions"], {
			"ok": self.key_ok,
			"cancel": self.key_exit,
			"red": self.key_red,
			"green": self.key_green,
			"yellow": self.key_yellow
		}, -1)
		self.onShown.append(self.update_skin_list)

	def update_skin_list(self):
		skin_list = []
		for index, filter in enumerate(self.filtersettings):
			defaulttext = "{Startfilter}" if index == self.defaultfilter else ""
			skin_list.append((filter[0][0], defaulttext, "✔" if filter[1] else "✘", int("0x0004c81b", 0) if filter[1] else int("0x00f50808", 0)))  # alternatively "✓", "✗"
		self["filter_list"].updateList(skin_list)

	def key_ok(self):
		curr_index = self["filter_list"].getCurrentIndex()
		if curr_index == self.defaultfilter and self.filtersettings[curr_index][1]:
			self.tvinfobox.show_dialog(f"Filter '{self.filtersettings[curr_index][0][0]}' kann als Startfilter nicht deaktiviert werden.")
		elif self.filtersettings:
			self.filtersettings[curr_index][1] = not self.filtersettings[curr_index][1]
			self.update_skin_list()

	def key_red(self):
		if self.filtersettings:
			if self.deselect:
				for index in range(len(self.filtersettings)):
					self.filtersettings[index][1] = False
				self["key_red"].setText("Alle auswählen")
			else:
				for index in range(len(self.filtersettings)):
					self.filtersettings[index][1] = True
				self["key_red"].setText("Alle abwählen")
		self.deselect = not self.deselect
		self.update_skin_list()

	def key_green(self):
		if self.filtersettings:
			if all(not filter[1] for filter in self.filtersettings):
				self.tvinfobox.show_dialog("Bitte wählen Sie mindestens einen Filter aus.")
			else:
				config.plugins.tvspielfilm.defaultfilter.value = str(self.defaultfilter)
				config.plugins.tvspielfilm.defaultfilter.save()
				config.plugins.tvspielfilm.filtersettings.value = dumps(self.filtersettings)
				config.plugins.tvspielfilm.filtersettings.save()
				self.key_exit()

	def key_yellow(self):
		curr_index = self["filter_list"].getCurrentIndex()
		if self.filtersettings[curr_index][1]:
			if curr_index == self.defaultfilter:
				self.tvinfobox.show_dialog(f"Filter '{self.filtersettings[curr_index][0][0]}' ist bereits als Startfilter definiert.")
			else:
				self.defaultfilter = curr_index
				self.update_skin_list()
		else:
			self.tvinfobox.show_dialog(f"Filter '{self.filtersettings[curr_index][0][0]}' ist deaktiviert und kann deswegen kein Startfilter sein.")

	def key_exit(self):
		self.close()


def set_tvs_font():
	font_path = tvglobals.FONTPATH
	if not isfile(font_path):
		print(f"[TVSpielfilm] Font not found: {font_path}. Retaining skin Regular.")
		return
	font_name = "TVSpielfilmCompact"
	font_key = (font_path, font_name)
	if font_key not in TVS_REGISTERED_FONTS:
		try:
			addFont(font_path, font_name, 100, False)
		except Exception as error:
			print(f"[TVSpielfilm] Font registration failed: {error}. Retaining skin Regular.")
			return
		TVS_REGISTERED_FONTS.add(font_key)
	for screen_class in (TVMagazine, TVFullscreen, TVTipsBox, TVInfoBox, TVUpdate, TVOverview, TVMain, TVSelectChannelCategory, TVImport, TVChannelselection, TVFilterselection):
		screen_class.skin = (screen_class.skin.replace('font="Regular;', f'font="{font_name};').replace('gFont("Regular",', f'gFont("{font_name}",'))
	print(f"[TVSpielfilm] Using installed font: {font_path}")


set_tvs_font()
