"""
Defines records for assessment parts
"""
import json

from dlkit.abstract_osid.assessment_authoring import record_templates as abc_assessment_authoring_records
from dlkit.mongo.assessment_authoring.objects import AssessmentPartList
from dlkit.mongo.assessment_authoring.sessions import AssessmentPartLookupSession
from dlkit.mongo.osid import record_templates as osid_records
from dlkit.mongo.osid.metadata import Metadata
from dlkit.mongo.primitives import Id
from dlkit.mongo.osid.osid_errors import IllegalState, InvalidArgument, NoAccess
from dlkit.mongo.assessment.assessment_utilities import get_assessment_section

from ...osid.base_records import ObjectInitRecord


from urllib import quote, unquote


def get_part_from_magic_part_lookup_session(section_id, part_id, *args, **kwargs):
    mpls = MagicAssessmentPartLookupSession(section_id, part_id, *args, **kwargs)
    return mpls.get_assessment_part(part_id)


class ScaffoldDownAssessmentPartRecord(ObjectInitRecord):
    """magic assessment part record for scaffold down adaptive questions"""
    _implemented_record_type_identifiers = [
        'scaffold-down'
    ]
    def __init__(self, *args, **kwargs):
        super(ScaffoldDownAssessmentPartRecord, self).__init__(*args, **kwargs)
        self._magic_identifier = None

    def get_id(self):
        """override get_id to generate our "magic" id that encodes scaffolding information"""
        return Id(namespace='assessment_authoring.AssessmentPart',
                  identifier=self._magic_identifier,
                  authority='magic-part-authority')

    def initialize(self, magic_identifier, assessment_section_id):
        """This method is to be called by a magic AssessmentPart lookup session.
        
        magic_identifier_part includes:
            max_levels = how many levels are left
            objective_id = the Objective Id to for which to select an item
            item_index = the index of this item in its parent part
        
        """
        arg_map = json.loads(unquote(magic_identifier).split('?')[-1])
        self._magic_identifier = magic_identifier
        self._assessment_section_id = assessment_section_id
        self.my_osid_object._my_map['maxLevels'] = arg_map['max_levels']
        self.my_osid_object._my_map['learningObjectiveId'] = arg_map['objective_id']
        self.my_osid_object._my_map['itemIndex'] = arg_map['item_index']
        
        mgr = self.my_osid_object._get_provider_manager('ASSESSMENT', local=True)
        if self.my_osid_object._my_map['bankId']:
            item_query_session = mgr.get_item_query_session_for_bank(Id(self.my_osid_object._my_map['bankId']),
                                                                     proxy=self.my_osid_object._proxy)
        else:
            item_query_session = mgr.get_item_query_session(proxy=self.my_osid_object._proxy)
        item_query_session.use_federated_bank_view()
        item_query = item_query_session.get_item_query()
        item_query.match_earning_objective_id(self.my_osid_object._my_map['learningObjectiveId'], True)
        item_list = item_query_session.get_items_by_query(item_query)

        seen_questions = get_assessment_section(assessment_section_id)._my_map['questions']
        seen_items = [question['itemId'] for question in seen_questions]
        unseen_item_id = None
        for item in item_list:
            if item not in seen_items:
                unseen_item_id = item.get_id()
        if unseen_item_id is not None:
            self.my_osid_object._my_map['itemId'] = str(unseen_item_id)
        else:
            self.my_osid_object._my_map['itemId'] = ''

    def has_children(self):
        if self.my_osid_object._my_map['maxLevels']:
            mgr = self.my_osid_object._get_provider_manager('ASSESSMENT', local=True)
            if self.my_osid_object._my_map['bankId']:
                item_lookup_session = mgr.get_item_lookup_session_for_bank(Id(self.my_osid_object._my_map['bankId']),
                                                                           proxy=self.my_osid_object._proxy)
            else:
                item_lookup_session = mgr.get_item_lookup_session(proxy=self.my_osid_object._proxy)
            item_lookup_session.use_federated_bank_view()
            item = item_lookup_session.get_item(self.my_osid_object.get_item_id())
            if not item.is_response_correct(None):  # item has not been answered correctly:
                return True
        return False

    def get_child_ids(self):
        if self.has_children():
            objective_id = self.my_osid_object.learning_objective_id
            orig_id = self.my_osid_object.get_id()
            authority = 'magic-part-authority'
            namespace = orig_id.get_identifier_namespace()
            arg_map = {'max_levels': self.my_osid_object._my_map['maxLevels'] - 1,
                       'objective_id': str(objective_id)}
            orig_identifier = unquote(orig_id.get_identifier()).split('?')[0]
            child_ids = []
            for num in range(self.my_osid_object._my_map['maxWaypointItems']):
                arg_map['item_index'] = num
                magic_identifier_part = quote('{0}?{1}'.format(orig_identifier,
                                                               json.dumps(arg_map)))
                child_ids.append(Id(authority=authority,
                                    identifier_namespace=namespace,
                                    identifier=magic_identifier_part))
            return child_ids
        raise IllegalState()

    def get_children(self):
        part_list = []
        for child_id in self.get_child_ids():
            part = get_part_from_magic_part_lookup_session(self._assessment_section_id,
                                                           child_id,
                                                           runtime=self.my_osid_object._runtime,
                                                           proxy=self.my_osid_object._proxy)
            # it is expected that the magic_part_lookup_session will call part.initialize()
            part_list.append(part)
        return AssessmentPartList(part_list,
                                  runtime=self.my_osid_object._runtime,
                                  proxy=self.my_osid_object._proxy)

    def has_item_id(self):
        return bool(self.my_osid_object._my_map['itemId'])

    def get_item_id(self):
        if self.has_item_id():
            return Id(self.my_osid_object._my_map['itemId'])
        raise IllegalState()

    @property
    def learning_objective_id(self):
        return Id(self.my_osid_object._my_map['learningObjectiveId'])


class ScaffoldDownAssessmentPartFormRecord(abc_assessment_authoring_records.AssessmentPartFormRecord,
                                           osid_records.OsidRecord):
    """magic assessment part form record for scaffold down adaptive assessments"""

    _implemented_record_type_identifiers = [
        'scaffold-down'
    ]

    def __init__(self, osid_object_form=None):
        if osid_object_form is not None:
            self.my_osid_object_form = osid_object_form
        self._init_metadata()
        if not self.my_osid_object_form.is_for_update():
            self._init_map()
        super(ScaffoldDownAssessmentPartFormRecord, self).__init__()

    def _init_metadata(self):
        self._item_metadata = {
            'element_id': Id(self.my_osid_object_form._authority,
                             self.my_osid_object_form._namespace,
                             'item'),
            'element_label': 'Item',
            'instructions': 'accepts an Item id',
            'required': False,
            'read_only': False,
            'linked': False,
            'array': False,
            'default_id_values': [''],
            'syntax': 'ID',
            'id_set': []
        }
        self._learning_objective_id_metadata = {
            'element_id': Id(self.my_osid_object_form._authority,
                             self.my_osid_object_form._namespace,
                             'learning-objective'),
            'element_label': 'Learning Objective',
            'instructions': 'accepts a Learning Objective id',
            'required': False,
            'read_only': False,
            'linked': False,
            'array': False,
            'default_id_values': [''],
            'syntax': 'ID',
            'id_set': []
        }
        self._max_levels_metadata = {
            'element_id': Id(self.my_osid_object_form._authority,
                             self.my_osid_object_form._namespace,
                             'max-levels'),
            'element_label': 'Max Levels',
            'instructions': 'accepts an integer value',
            'required': True,
            'read_only': False,
            'linked': False,
            'array': False,
            'default_cardinal_values': [None],
            'syntax': 'CARDINAL',
            'minimum_cardinal': 0,
            'maximum_cardinal': None,
            'cardinal_set': []
        }
        self._max_waypoint_items_metadata = {
            'element_id': Id(self.my_osid_object_form._authority,
                             self.my_osid_object_form._namespace,
                             'max-waypoint-items'),
            'element_label': 'Max Waypoint Items',
            'instructions': 'accepts an integer value',
            'required': True,
            'read_only': False,
            'linked': False,
            'array': False,
            'default_cardinal_values': [1],
            'syntax': 'CARDINAL',
            'minimum_cardinal': 0,
            'maximum_cardinal': None,
            'cardinal_set': []
        }
        self._item_bank_metadata = {
            'element_id': Id(self.my_osid_object_form._authority,
                             self.my_osid_object_form._namespace,
                             'item-bank'),
            'element_label': 'Item Bank',
            'instructions': 'accepts an assessment Bank Id',
            'required': False,
            'read_only': False,
            'linked': False,
            'array': False,
            'default_id_values': [''],
            'syntax': 'ID',
            'id_set': []
        }

    def _init_map(self):
        """stub"""
        super(ScaffoldDownAssessmentPartFormRecord, self)._init_map()
        self.my_osid_object_form._my_map['itemId'] = \
            self._item_metadata['default_id_values'][0]
        self.my_osid_object_form._my_map['learningObjectiveId'] = \
            self._learning_objective_id_metadata['default_id_values'][0]
        self.my_osid_object_form._my_map['maxLevels'] = \
            self._max_levels_metadata['default_id_values'][0]
        self.my_osid_object_form._my_map['maxWaypointItems'] = \
            self._max_waypoint_items_metadata['default_id_values'][0]
        self.my_osid_object_form._my_map['itemBankId'] = \
            self._item_bank_metadata['default_id_values'][0]

    def get_item_metadata(self):
        """get the metadata for item"""
        metadata = dict(self._item_metadata)
        metadata.update({'existing_id_values': self.my_osid_object_form._my_map['itemIds']})
        return Metadata(**metadata)

    def set_item(self, item_id):
        '''the target Item
        
        This can only be set if there is no learning objective set
        
        '''
        if self.get_item_metadata().is_read_only():
            raise NoAccess()
        if not self.my_osid_object_form._is_valid_id(item_id):
            raise InvalidArgument()
        if self.my_osid_object_form._my_map['learningObjectiveId']:
            raise IllegalState()
        self.my_osid_object_form._my_map['itemId'] = [str(item_id)]

    def clear_item(self):
        if (self.get_item_metadata().is_read_only() or
                self.get_item_metadata().is_required()):
            raise NoAccess()
        self.my_osid_object_form._my_map['itemId'] = \
            self.get_item_metadata().get_default_id_values()[0]

    def get_learning_objective_id_metadata(self):
        """get the metadata for learning objective"""
        metadata = dict(self._learning_objective_id_metadata)
        metadata.update({'existing_id_values': self.my_osid_object_form._my_map['learningObjectiveId']})
        return Metadata(**metadata)

    def set_learning_objective_id(self, learning_objective_id):
        """the learning objective to find related items for
        
        This can only be set if there are no items specifically set
        
        """
        if self.get_learning_objective_id_metadata().is_read_only():
            raise NoAccess()
        if not self.my_osid_object_form._is_valid_id(learning_objective_id):
            raise InvalidArgument()
        if self.my_osid_object_form._my_map['itemId']:
            raise IllegalState()
        self.my_osid_object_form._my_map['learningObjectiveId'] = [str(learning_objective_id)]

    def clear_learning_objective_id(self):
        if (self.get_learning_objective_id_metadata().is_read_only() or
                self.get_learning_objective_id_metadata().is_required()):
            raise NoAccess()
        self.my_osid_object_form._my_map['learningObjectiveId'] = \
            self.get_learning_objective_id_metadata().get_default_id_values()[0]

    def get_max_levels_metadata(self):
        """get the metadata for max levels"""
        metadata = dict(self._max_levels_metadata)
        metadata.update({'existing_cardinal_values': self.my_osid_object_form._my_map['maxLevels']})
        return Metadata(**metadata)

    def set_max_levels(self, max_levels):
        if self.get_max_levels_metadata().is_read_only():
            raise NoAccess()
        if not self.my_osid_object_form._is_valid_cardinal(max_levels):
            raise InvalidArgument()
        self.my_osid_object_form._my_map['maxLevels'] = max_levels
 
    def clear_max_levels(self):
        if (self.get_max_levels_metadata().is_read_only() or
                self.get_max_levels_metadata().is_required()):
            raise NoAccess()
        self.my_osid_object_form._my_map['maxLevels'] = \
            self.get_max_levels_metadata().get_default_cardinal_values()[0]

    def get_max_waypoint_items_metadata(self):
        """get the metadata for max waypoint items"""
        metadata = dict(self._max_waypoint_items_metadata)
        metadata.update({'existing_cardinal_values': self.my_osid_object_form._my_map['maxWaypointItems']})
        return Metadata(**metadata)

    def set_max_waypoint_items(self, max_waypoint_items):
        """This determines how many waypoint items will be seen for a scaffolded wrong answer"""
        if self.get_max_waypoint_items_metadata().is_read_only():
            raise NoAccess()
        if not self.my_osid_object_form._is_valid_cardinal(max_waypoint_items):
            raise InvalidArgument()
        self.my_osid_object_form._my_map['maxWaypointItems'] = max_waypoint_items

    def clear_max_waypoint_items(self):
        if (self.get_max_waypoint_items_metadata().is_read_only() or
                self.get_max_waypoint_items_metadata().is_required()):
            raise NoAccess()
        self.my_osid_object_form._my_map['maxWaypointItems'] = \
            self.get_max_waypoint_items_metadata().get_default_cardinal_values()[0]

    def get_item_bank_metadata(self):
        """get the metadata for item bank"""
        metadata = dict(self._item_bank_metadata)
        metadata.update({'existing_id_values': self.my_osid_object_form._my_map['itemBankId']})
        return Metadata(**metadata)

    def set_item_bank(self, bank_id):
        """the assessment bank in which to search for items, such as related to an objective"""
        if self.get_item_bank_metadata().is_read_only():
            raise NoAccess()
        if not self.my_osid_object_form._is_valid_id(bank_id):
            raise InvalidArgument()
        self.my_osid_object_form._my_map['itemBankId'] = [str(bank_id)]

    def clear_item_bank(self):
        if (self.get_item_bank_metadata().is_read_only() or
                self.get_item_bank_metadata().is_required()):
            raise NoAccess()
        self.my_osid_object_form._my_map['itemBankId'] = \
            self.get_item_bank_metadata().get_default_id_values()[0]


class MagicAssessmentPartLookupSession(AssessmentPartLookupSession):
    """This magic session should be used for getting magic AssessmentParts"""

    def __init__(self, assessment_section_id, *args, **kwargs):
        super(MagicAssessmentPartLookupSession, self).__init__(*args, **kwargs)
        self._my_assessment_section_id = assessment_section_id

    def get_assessment_part(self, assessment_part_id):
        authority = assessment_part_id.get_authority()
        if authority == 'magic-part-authority':
            magic_identifier = unquote(assessment_part_id.identifier)
            orig_identifier = magic_identifier.split('?')[0]
            assessment_part = super(MagicAssessmentPartLookupSession, self).get_assessment_part(self,
                                                                                                assessment_part_id=Id(authority=self._catalog.ident.authority,
                                                                                                                      namespace=assessment_part_id.get_identifier_namespace(),
                                                                                                                      identifier=orig_identifier))
            assessment_part.initialize(assessment_part_id.identifier, self._my_assessment_section_id)
        else:
            return super(MagicAssessmentPartLookupSession, self).get_assessment_part(self, assessment_part_id)

    def get_assessment_parts_by_ids(self, assessment_part_ids):
        part_list = []
        for assessment_part_id in assessment_part_ids:
            part_list.append(self.get_assessment_part(assessment_part_id))
        return AssessmentPartList(part_list, runtime=self._runtime, proxy=self._proxy)
