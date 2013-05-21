import datetime
from lxml import etree
import json
import os.path
import time
import uuid

from vesna import cdf

_METADATA_HEADER = "Additional VESNA metadata follows:\n\n"

def _metadata_encode(obj, string=''):
	i = string.find(_METADATA_HEADER)
	if i != -1:
		string = string[:i]

	return string + _METADATA_HEADER + json.dumps(obj, indent=4)

def _metadata_decode(string):
	i = string.find(_METADATA_HEADER)
	if i != -1:
		return json.loads(string[i+len(_METADATA_HEADER):])

def text_or_none(xml_tree, xpath):
	t = xml_tree.find(xpath)
	if t:
		return t.text
	else:
		return None

class CDFXMLDevice(cdf.CDFDevice):
	def __init__(self, base_url, cluster_id, addr):
		self.base_url = base_url
		self.cluster_id = cluster_id
		self.addr = addr

	@classmethod
	def _from_xml(cls, tree):
		obj = _metadata_decode(tree.find("description").text)
		return cls(obj['base_url'], obj['cluster_id'], obj['addr'])

	def _to_xml(self):
		tree = etree.Element("device")

		name = etree.SubElement(tree, "name")
		name.text = "VESNA node %d" % (self.addr,)

		description = etree.SubElement(tree, "description")
		description.text = _metadata_encode({
			"base_url": self.base_url,
			"cluster_id": self.cluster_id,
			"addr": self.addr})

		return tree

class CDFExperimentIteration:
	def __init__(self, start_time, end_time, slot_id=10):
		self.start_time = start_time
		self.end_time = end_time
		self.slot_id = slot_id

class CDFXMLExperiment(cdf.CDFExperiment):
	def __init__(self, title, summary, start_hz, stop_hz, step_hz, tag=None, _xml_tree=None):
		self.devices = []
		self.title = title
		self.summary = summary
		self.start_hz = start_hz
		self.stop_hz = stop_hz
		self.step_hz = step_hz

		if tag is None:
			tag = "vesna-alh-tools-" + str(uuid.uuid4())

		self.tag = tag

		self._unsaved_iterations = []

		if _xml_tree:
			self.xml_tree = _xml_tree
		else:

			now = datetime.datetime.now()

			t = """<experimentDescription>
	<experimentAbstract>
	</experimentAbstract>
	<metaInformation>
		<radioFrequency>
			<startFrequency>%(start_hz)d</startFrequency>
			<stopFrequency>%(stop_hz)d</stopFrequency>
		</radioFrequency>
		<date>%(date)s</date>
		<traceDescription>
			<format>Tab-separated-values file with timestamp, frequency, power triplets.</format>
			<fileFormat>
				<header>Comment line, starting with #</header>
				<collectedMetrics>
					<name>time</name>
					<unitOfMeasurements>s</unitOfMeasurements>
				</collectedMetrics>
				<collectedMetrics>
					<name>frequency</name>
					<unitOfMeasurements>Hz</unitOfMeasurements>
				</collectedMetrics>
				<collectedMetrics>
					<name>power</name>
					<unitOfMeasurements>dBm</unitOfMeasurements>
				</collectedMetrics>
			</fileFormat>
		</traceDescription>
	</metaInformation>
</experimentDescription>""" % {	"start_hz": start_hz, "stop_hz": stop_hz, "date": now.isoformat() }

			# remove whitespace - it's added later through pretty_print
			t = t.replace("\t", "").replace("\n", "")
			self.xml_tree = etree.ElementTree(etree.XML(t))

			abstract = self.xml_tree.find("experimentAbstract")

			title_ = etree.SubElement(abstract, "title")
			title_.text = title

			tag_ = etree.SubElement(abstract, "uniqueCREWTag")
			tag_.text = tag

			date_ = etree.SubElement(abstract, "releaseDate")
			date_.text = now.isoformat()

			summary_ = etree.SubElement(abstract, "experimentSummary")
			summary_.text = summary

			etree.SubElement(abstract, "relatedExperiments")

			notes_ = etree.SubElement(abstract, "notes")
			notes_.text = _metadata_encode({"step_hz": step_hz})

	@classmethod
	def load(cls, f):
		xml_tree = etree.parse(f)


		title = text_or_none(xml_tree, "experimentAbstract/title")

		tag = text_or_none(xml_tree, "experimentAbstract/uniqueCREWTag")

		authors = []
		for author in xml_tree.findall("experimentAbstract/author"):
			authors.append(CDFXMLAuthor.from_tree(author))

		release_date_t = text_or_none(xml_tree, "experimentAbstract/releaseDate")
		release_date = datetime.datetime.strptime(release_date_t, "%Y-%m-%d")

		summary = text_or_none(xml_tree, "experimentAbstract/experimentSummary")

		methodology = []
		for m in xml_tree.findall("experimentAbstract/collectionMethodology"):
			methodology.append(m.text)

		documentation = []
		for document in xml_tree.findall("experimentAbstract/furtherDocumentation"):
			documentation.append(CDFXMLDocument.from_tree(document))

		related_experiments = text_or_none(xml_tree, "experimentAbstract/relatedExperiments")

		extra = None

		notes = []
		for note in xml_tree.findall("experimentAbstract/notes"):
			notes.append(note.text)

			o = _metadata_decode(note.text)
			if o:
				extra = o

		experiment = cls(
				title=title, 
				tag=tag,
				authors=authors,
				release_date=release_date,
				summary=summary,
				methodology=methodology,
				documentation=documentation,
				related_experiments=related_experiments,
				notes=notes)


		start_hz = int(text_or_none(xml_tree, "metaInformation/radioFrequency/startFrequency"))
		stop_hz = int(text_or_none(xml_tree, "metaInformation/radioFrequency/stopFrequency"))
		step_hz = extra['step_hz']

		experiment.set_frequency_range(start_hz, stop_hz, step_hz)


		duration = datetime.timedelta(seconds=extra['duration'])
		experiment.set_duration(duration)


		devices = {}
		for d in xml_tree.findall("metaInformation/device"):
			device = CDFXMLDevice.from_tree(d)
			devices[device.key()] = device


		extra_interferers = _metadata_decode(xml_tree.find("metaInformation/radioFrequency/interferenceSources"))
		for extra_interferer in extra_interferers:
			device = devices.pop(extra_interferer.device)

			start_time = datetime.timedelta(seconds=extra_interferer['start_time'])
			end_time = datetime.timedelta(seconds=extra_interferer['end_time'])

			interferer = CDFInterferer(
					device=device,
					center_hz=extra_interferer['center_hz'],
					power_dbm=extra_interferer['power_dbm'],
					device_id=extra_interferer['device_id'],
					config_id=extra_interferer['config_id'],
					start_time=start_time,
					end_time=end_time)

			experiment.add_interferer(interferer)

		for device in devices.itervalues():
			experiment.add_device(device)

		return experiment

	def save(self, f):
		self.xml_tree.write(f, pretty_print=True, encoding='utf8')

	def save_all(self, path=None):
		if path is None:
			path = self.tag

		cdf_path = path + ".cdf"
		dat_path = path + ".dat"

		try:
			os.mkdir(dat_path)
		except OSError:
			pass

		for iteration in self._unsaved_iterations:
			iteration_ = etree.SubElement(self.xml_tree.getroot(), "experimentIteration")

			time_ = etree.SubElement(iteration_, "time")

			starttime_ = etree.SubElement(time_, "starttime")
			starttime_.text = datetime.datetime.fromtimestamp(iteration.start_time).isoformat()

			endtime_ = etree.SubElement(time_, "endtime")
			endtime_.text = datetime.datetime.fromtimestamp(iteration.end_time).isoformat()

			for i, sensor in enumerate(iteration.sensors):

				n = "data_%d_node_%d_%d.dat" % (
						iteration.start_time,
						sensor.sensor.alh.addr,
						i)
				p = os.path.join(dat_path, n)

				sensor.result.write(p)

				tracefile_ = etree.SubElement(iteration_, "traceFile")
				tracefile_.text = p

		self.save(open(cdf_path, "w"))

		self._unsaved_iterations = []
