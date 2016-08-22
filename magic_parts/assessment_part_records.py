"""
Defines records for assessment parts
"""
import json

from bson import ObjectId

from random import shuffle

from dlkit.abstract_osid.assessment_authoring import record_templates as abc_assessment_authoring_records
from dlkit.mongo.assessment_authoring.objects import AssessmentPartList
from dlkit.mongo.assessment_authoring.sessions import AssessmentPartLookupSession
from dlkit.mongo.id.objects import IdList
from dlkit.mongo.osid import record_templates as osid_records
from dlkit.mongo.osid.metadata import Metadata
from dlkit.mongo.primitives import Id
from dlkit.mongo.osid.osid_errors import IllegalState, InvalidArgument, NoAccess, NotFound
from dlkit.mongo.utilities import MongoClientValidated

from ...osid.base_records import ObjectInitRecord


from urllib import quote, unquote


def get_part_from_magic_part_lookup_session(section, part_id, *args, **kwargs):
    mpls = MagicAssessmentPartLookupSession(section, *args, **kwargs)
    return mpls.get_assessment_part(part_id)


class ScaffoldDownAssessmentPartRecord(ObjectInitRecord):
    """magic assessment part record for scaffold down adaptive questions"""
    _implemented_record_type_identifiers = [
        'scaffold-down'
    ]
    def __init__(self, *args, **kwargs):
        super(ScaffoldDownAssessmentPartRecord, self).__init__(*args, **kwargs)
        self._magic_identifier = None
        self._assessment_section = None

    def get_id(self):
        """override get_id to generate our "magic" id that encodes scaffolding information"""
        item_index = 0
        if 'itemIndex' in self.my_osid_object._my_map:
            item_index = self.my_osid_object._my_map['itemIndex']
        magic_identifier = {
            'max_levels': self.my_osid_object._my_map['maxLevels'],
            'objective_ids': self.my_osid_object._my_map['learningObjectiveIds'],
            'item_index': item_index
        }
        identifier = quote('{0}?{1}'.format(str(self.my_osid_object._my_map['_id']),
                                            json.dumps(magic_identifier)))
        return Id(namespace='assessment_authoring.AssessmentPart',
                  identifier=identifier,
                  authority='magic-part-authority')

    ident = property(fget=get_id)
    id_ = property(fget=get_id)

    def initialize(self, magic_identifier, assessment_section):
        """This method is to be called by a magic AssessmentPart lookup session.
        
        magic_identifier_part includes:
            max_levels = how many levels are left
            objective_id = the Objective Id to for which to select an item
            item_index = the index of this item in its parent part
        
        """
        arg_map = json.loads(unquote(magic_identifier).split('?')[-1])
        self._magic_identifier = magic_identifier
        self._assessment_section = assessment_section
        self.my_osid_object._my_map['maxLevels'] = arg_map['max_levels']
        self.my_osid_object._my_map['learningObjectiveIds'] = arg_map['objective_ids']
        self.my_osid_object._my_map['itemIndex'] = arg_map['item_index']

        if self.my_osid_object._my_map['learningObjectiveIds'] != ['']:
            try:
                self.my_osid_object._my_map['itemIds'] = [self.get_my_item_id_from_section(assessment_section)]
            except IllegalState:
                self.load_item_for_objective()

    def load_item_for_objective(self):
        """if this is the first time for this magic part, find an LO linked item"""
        mgr = self.my_osid_object._get_provider_manager('ASSESSMENT', local=True)
        if self.my_osid_object._my_map['itemBankId']:
            item_query_session = mgr.get_item_query_session_for_bank(Id(self.my_osid_object._my_map['itemBankId']),
                                                                     proxy=self.my_osid_object._proxy)
        else:
            item_query_session = mgr.get_item_query_session(proxy=self.my_osid_object._proxy)
        item_query_session.use_federated_bank_view()
        item_query = item_query_session.get_item_query()
        for objective_id_str in self.my_osid_object._my_map['learningObjectiveIds']:
            item_query.match_learning_objective_id(Id(objective_id_str), True)
        item_list = list(item_query_session.get_items_by_query(item_query))

        # I'm not sure this works? If all sibling items are generated at once, then
        # won't all items with this LO be seen / in the section map?
        seen_questions = self._assessment_section._my_map['questions']
        seen_items = [question['itemId'] for question in seen_questions]
        unseen_item_id = None
        # need to randomly shuffle this item_list
        shuffle(item_list)
        for item in item_list:
            if str(item.ident) not in seen_items:
                unseen_item_id = item.get_id()
                break
        if unseen_item_id is not None:
            self.my_osid_object._my_map['itemIds'] = [str(unseen_item_id)]
        elif self.my_osid_object._my_map['allowRepeatItems']:
            self.my_osid_object._my_map['itemIds'] = [str(item_list[0].ident)]
        else:
            self.my_osid_object._my_map['itemIds'] = ['']

    def has_children(self):
        """checks if child parts are currently available for this part"""
        if self._assessment_section is not None:
            if (self.my_osid_object._my_map['maxLevels'] is None or
                    self.my_osid_object._my_map['maxLevels'] > 0):
                try:
                    section = self._assessment_section
                    item_id = self.get_my_item_id_from_section(section)
                    if not section._is_correct(item_id) and section._get_confused_learning_objective_ids(item_id):
                        return True
                except IllegalState:
                    pass
        return False

    def get_child_ids(self):
        """creates max_waypoint_items number of new child parts"""
        if self.has_children():
            objective_id = self.get_scaffold_objective_ids().next() # Assume just one for now
            orig_id = self.my_osid_object.get_id()
            authority = 'magic-part-authority'
            namespace = orig_id.get_identifier_namespace()
            if self.my_osid_object._my_map['maxLevels'] is None:
                max_levels = None
            else:
                max_levels = self.my_osid_object._my_map['maxLevels'] - 1
            arg_map = {'max_levels': max_levels,
                       'objective_ids': [str(objective_id)]}
            orig_identifier = unquote(orig_id.get_identifier()).split('?')[0]
            child_ids = []
            for num in range(self.my_osid_object._my_map['maxWaypointItems']):
                arg_map['item_index'] = num
                magic_identifier_part = quote('{0}?{1}'.format(orig_identifier,
                                                               json.dumps(arg_map)))
                child_ids.append(Id(authority=authority,
                                    namespace=namespace,
                                    identifier=magic_identifier_part))
            return IdList(child_ids,
                          runtime=self.my_osid_object._runtime,
                          proxy=self.my_osid_object._runtime)
        raise IllegalState()

    def get_children(self):
        part_list = []
        for child_id in self.get_child_ids():
            part = get_part_from_magic_part_lookup_session(self._assessment_section,
                                                           child_id,
                                                           runtime=self.my_osid_object._runtime,
                                                           proxy=self.my_osid_object._proxy)
            # it is expected that the magic_part_lookup_session will call part.initialize()
            part_list.append(part)
        return AssessmentPartList(part_list,
                                  runtime=self.my_osid_object._runtime,
                                  proxy=self.my_osid_object._proxy)

    def has_item_ids(self):
        return bool(self.my_osid_object._my_map['itemIds'])

    def get_item_ids(self):
        if self.has_item_ids():
            return IdList(self.my_osid_object._my_map['itemIds'],
                          runtime=self.my_osid_object._runtime,
                          proxy=self.my_osid_object._proxy)
        raise IllegalState()

    @property
    def learning_objective_ids(self):
        return IdList(self.my_osid_object._my_map['learningObjectiveIds'],
                      runtime=self.my_osid_object._runtime,
                      proxy=self.my_osid_object._proxy)

    def get_scaffold_objective_ids(self):
        """Assumes that a scaffold objective id is available"""
        section = self._assessment_section
        item_id = self.get_my_item_id_from_section(section)
        return section._get_confused_learning_objective_ids(item_id)

    def get_my_item_id_from_section(self, section):
        """returns the first item associated with this magic Part Id in the Section"""
        for question_map in section._my_map['questions']:
            if question_map['assessmentPartId'] == str(self.get_id()):
                return Id(question_map['questionId'])
        raise IllegalState('This Part currently has no Item in the Section')

    def delete(self):
        """need this because the MongoClientValidated cannot deal with the magic identifier"""
        magic_identifier = unquote(self.get_id().identifier)
        orig_identifier = magic_identifier.split('?')[0]
        collection = MongoClientValidated('assessment_authoring',
                                          collection='AssessmentPart',
                                          runtime=self.my_osid_object._runtime)
        collection.delete_one({'_id': ObjectId(orig_identifier)})

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
        self._item_ids_metadata = {
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
        self._learning_objective_ids_metadata = {
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
        self._item_bank_id_metadata = {
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
        self._allow_repeat_items_metadata = {
            'element_id': Id(self.my_osid_object_form._authority,
                             self.my_osid_object_form._namespace,
                             'allow-repeat-items'),
            'element_label': 'Allow Repeat Items',
            'instructions': 'accepts a boolean value',
            'required': True,
            'read_only': False,
            'linked': False,
            'array': False,
            'default_boolean_values': [True],
            'syntax': 'BOOLEAN'
        }

    def _init_map(self):
        """stub"""
        # super(ScaffoldDownAssessmentPartFormRecord, self)._init_map()
        self.my_osid_object_form._my_map['itemIds'] = \
            [str(self._item_ids_metadata['default_id_values'][0])]
        self.my_osid_object_form._my_map['learningObjectiveIds'] = \
            [str(self._learning_objective_ids_metadata['default_id_values'][0])]
        self.my_osid_object_form._my_map['maxLevels'] = \
            self._max_levels_metadata['default_cardinal_values'][0]
        self.my_osid_object_form._my_map['maxWaypointItems'] = \
            self._max_waypoint_items_metadata['default_cardinal_values'][0]
        self.my_osid_object_form._my_map['itemBankId'] = \
            self._item_bank_id_metadata['default_id_values'][0]
        self.my_osid_object_form._my_map['allowRepeatItems'] = \
            bool(self._allow_repeat_items_metadata['default_boolean_values'][0])

    def get_item_ids_metadata(self):
        """get the metadata for item"""
        metadata = dict(self._item_ids_metadata)
        metadata.update({'existing_id_values': self.my_osid_object_form._my_map['itemIds']})
        return Metadata(**metadata)

    def set_item_ids(self, item_ids):
        '''the target Item
        
        This can only be set if there is no learning objective set
        
        '''
        if self.get_item_ids_metadata().is_read_only():
            raise NoAccess()
        for item_id in item_ids:
            if not self.my_osid_object_form._is_valid_id(item_id):
                raise InvalidArgument()
        if self.my_osid_object_form._my_map['learningObjectiveIds'][0]:
            raise IllegalState()
        self.my_osid_object_form._my_map['itemIds'] = [str(i) for i in item_ids]

    def clear_item_ids(self):
        if (self.get_item_ids_metadata().is_read_only() or
                self.get_item_ids_metadata().is_required()):
            raise NoAccess()
        self.my_osid_object_form._my_map['itemIds'] = \
            [str(self.get_item_ids_metadata().get_default_id_values()[0])]

    def get_learning_objective_ids_metadata(self):
        """get the metadata for learning objective"""
        metadata = dict(self._learning_objective_ids_metadata)
        metadata.update({'existing_id_values': self.my_osid_object_form._my_map['learningObjectiveIds'][0]})
        return Metadata(**metadata)

    def set_learning_objective_ids(self, learning_objective_ids):
        """the learning objective to find related items for
        
        This can only be set if there are no items specifically set
        
        """
        if self.get_learning_objective_ids_metadata().is_read_only():
            raise NoAccess()
        for learning_objective_id in learning_objective_ids:
            if not self.my_osid_object_form._is_valid_id(learning_objective_id):
                raise InvalidArgument()
        if self.my_osid_object_form._my_map['itemIds'][0]:
            raise IllegalState()
        self.my_osid_object_form._my_map['learningObjectiveIds'] = [str(lo) for lo in learning_objective_ids]

    def clear_learning_objective_ids(self):
        if (self.get_learning_objective_ids_metadata().is_read_only() or
                self.get_learning_objective_ids_metadata().is_required()):
            raise NoAccess()
        self.my_osid_object_form._my_map['learningObjectiveIds'] = \
            [str(self.get_learning_objective_ids_metadata().get_default_id_values()[0])]

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

    def get_item_bank_id_metadata(self):
        """get the metadata for item bank"""
        metadata = dict(self._item_bank_id_metadata)
        metadata.update({'existing_id_values': self.my_osid_object_form._my_map['itemBankId']})
        return Metadata(**metadata)

    def set_item_bank_id(self, bank_id):
        """the assessment bank in which to search for items, such as related to an objective"""
        if self.get_item_bank_id_metadata().is_read_only():
            raise NoAccess()
        if not self.my_osid_object_form._is_valid_id(bank_id):
            raise InvalidArgument()
        self.my_osid_object_form._my_map['itemBankId'] = str(bank_id)

    def clear_item_bank_id(self):
        if (self.get_item_bank_id_metadata().is_read_only() or
                self.get_item_bank_id_metadata().is_required()):
            raise NoAccess()
        self.my_osid_object_form._my_map['itemBankId'] = \
            self.get_item_bank_id_metadata().get_default_id_values()[0]

    def get_allow_repeat_items_metadata(self):
        """get the metadata for allow repeat items"""
        metadata = dict(self._allow_repeat_items_metadata)
        metadata.update({'existing_id_values': self.my_osid_object_form._my_map['allowRepeatItems']})
        return Metadata(**metadata)

    def set_allow_repeat_items(self, allow_repeat_items):
        """determines if repeat items will be shown, or if the scaffold iteration will simply stop"""
        if self.get_allow_repeat_items_metadata().is_read_only():
            raise NoAccess()
        if not self.my_osid_object_form._is_valid_boolean(allow_repeat_items):
            raise InvalidArgument()
        self.my_osid_object_form._my_map['allowRepeatItems'] = allow_repeat_items

    def clear_allow_repeat_items(self):
        """reset allow repeat itmes to default value"""
        if (self.get_allow_repeat_items_metadata().is_read_only() or
                self.get_allow_repeat_items_metadata().is_required()):
            raise NoAccess()
        self.my_osid_object_form._my_map['allowRepeatItems'] = \
            bool(self._allow_repeat_items_metadata['default_boolean_values'][0])


class MagicAssessmentPartLookupSession(AssessmentPartLookupSession):
    """This magic session should be used for getting magic AssessmentParts"""

    def __init__(self, assessment_section=None, *args, **kwargs):
        super(MagicAssessmentPartLookupSession, self).__init__(*args, **kwargs)
        self._my_assessment_section = assessment_section

    def get_assessment_part(self, assessment_part_id):
        authority = assessment_part_id.get_authority()
        if authority == 'magic-part-authority':
            magic_identifier = unquote(assessment_part_id.identifier)
            orig_identifier = magic_identifier.split('?')[0]
            assessment_part = super(MagicAssessmentPartLookupSession, self).get_assessment_part(assessment_part_id=Id(authority=self._catalog.ident.authority,
                                                                                                                      namespace=assessment_part_id.get_identifier_namespace(),
                                                                                                                      identifier=orig_identifier))
            assessment_part.initialize(assessment_part_id.identifier, self._my_assessment_section)
            return assessment_part
        else:
            return super(MagicAssessmentPartLookupSession, self).get_assessment_part(assessment_part_id)

    def get_assessment_parts_by_ids(self, assessment_part_ids):
        part_list = []
        for assessment_part_id in assessment_part_ids:
            try:
                part_list.append(self.get_assessment_part(assessment_part_id))
            except NotFound:
                # sequestered?
                pass
        return AssessmentPartList(part_list, runtime=self._runtime, proxy=self._proxy)
