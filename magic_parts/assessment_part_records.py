"""
Defines records for assessment parts
"""
import json

from bson import ObjectId
from collections import OrderedDict
from random import shuffle
from urllib import quote, unquote

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

MAGIC_PART_AUTHORITY = 'magic-part-authority'
ENDLESS = 10000 # For seemingly endless waypoints

class QuotaCounter(object):
    # http://stackoverflow.com/questions/4020419/why-arent-python-nested-functions-called-closures
    pass

class UnansweredQuestionCounter(object):
    pass


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
        self._magic_parent_id = None
        self._level = 0

    def get_id(self):
        """override get_id to generate our "magic" id that encodes scaffolding information"""
        waypoint_index = 0
        if 'waypointIndex' in self.my_osid_object._my_map:
            waypoint_index = self.my_osid_object._my_map['waypointIndex']
        magic_identifier = {
            'level': self._level,
            'objective_ids': self.my_osid_object._my_map['learningObjectiveIds'],
            'waypoint_index': waypoint_index
        }
        if self._magic_parent_id is not None:
            magic_identifier['parent_id'] = str(self._magic_parent_id)

        identifier = quote('{0}?{1}'.format(str(self.my_osid_object._my_map['_id']),
                                            json.dumps(magic_identifier)))
        return Id(namespace='assessment_authoring.AssessmentPart',
                  identifier=identifier,
                  authority=MAGIC_PART_AUTHORITY)

    ident = property(fget=get_id)
    id_ = property(fget=get_id)

    def initialize(self, magic_identifier, assessment_section):
        """This method is to be called by a magic AssessmentPart lookup session.
        
        magic_identifier_part includes:
            parent_id = id string of the parent part that created this part
            level = how many levels deep
            objective_id = the Objective Id to for which to select an item
            waypoint_index = the index of this item in its parent part
        
        """
        arg_map = json.loads(unquote(magic_identifier).split('?')[-1], object_pairs_hook=OrderedDict)
        self._magic_identifier = magic_identifier
        self._assessment_section = assessment_section
        if 'level' in arg_map:
            self._level = arg_map['level']
        else:
            self._level = 0
        if 'parent_id' in arg_map:
            self._magic_parent_id = Id(arg_map['parent_id'])
        self.my_osid_object._my_map['learningObjectiveIds'] = arg_map['objective_ids']
        self.my_osid_object._my_map['waypointIndex'] = arg_map['waypoint_index']

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
                    self.my_osid_object._my_map['maxLevels'] > self._level):
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
        quota_counter = QuotaCounter()
        quota_counter.num_correct = 0

        unanswered_question_counter = UnansweredQuestionCounter()
        unanswered_question_counter.num_unanswered = 0

        def get_part_question_id(part_id_to_check):
            question_ids = self._assessment_section._get_question_ids_for_assessment_part(part_id_to_check)
            if not question_ids:
                return None
            return question_ids[0]  # There is only one expected, but this might change

        def quota_achieved(child_id_to_check):
            """keep track of number items correct and compare with waypoint quota"""
            if self.has_waypoint_quota():
                question_id = get_part_question_id(child_id_to_check)
                if question_id is None:
                    return False
                try:
                    if self._assessment_section._is_correct(question_id):
                        quota_counter.num_correct += 1
                except IllegalState:
                    pass
                if quota_counter.num_correct == self.my_osid_object._my_map['waypointQuota']:
                    return True
            return False

        def one_unanswered_question_in_children_already_exists(child_id_to_check):
            """keep track of unanswered children questions. Only permit 1"""
            question_id = get_part_question_id(child_id_to_check)
            if question_id is None:
                return False
            try:
                if not self._assessment_section._is_question_answered(question_id):
                    unanswered_question_counter.num_unanswered += 1
            except IllegalState:
                pass
            if unanswered_question_counter.num_unanswered == 1:
                return True
            else:
                return False

        if self.has_children():
            child_ids = []

            # correct answers may not generate an objective at all
            # and they should not generate children
            scaffold_objective_ids = self.get_scaffold_objective_ids()
            if scaffold_objective_ids.available() > 0:
                objective_id = scaffold_objective_ids.next() # Assume just one for now
                orig_id = self.my_osid_object.get_id()
                namespace = 'assessment_authoring.AssessmentPart'
                level = self._level + 1
                arg_map = {'parent_id': str(self.my_osid_object.get_id()),
                           'level': level,
                           'objective_ids': [str(objective_id)]}
                orig_identifier = unquote(orig_id.get_identifier()).split('?')[0]

                child_known_to_section = None
                max_waypoints = self.my_osid_object._my_map['maxWaypointItems']
                if max_waypoints is None:
                    max_waypoints = ENDLESS
                for num in range(max_waypoints):
                    arg_map['waypoint_index'] = num
                    magic_identifier_part = quote('{0}?{1}'.format(orig_identifier,
                                                                   json.dumps(arg_map)))
                    child_id = Id(authority=MAGIC_PART_AUTHORITY,
                                  namespace=namespace,
                                  identifier=magic_identifier_part)
                    child_ids.append(child_id)
                    section_part_ids = [p['assessmentPartId'] for p in self._assessment_section._my_map['assessmentParts']]
                    if str(child_id) in section_part_ids:
                        child_known_to_section = True
                    else:
                        child_known_to_section = False
                    # the problem with only checking quota_achieved is that each time this is called,
                    # it will generate another waypoint i.e. if 1.1. is wrong, then 1.2 -> 1.2, 1.3 -> 1.2, 1.3, 1.4
                    # because you haven't achieved the "right number" quota.
                    # However, the behavior we want is that we get only 1 more new question
                    # depending on if the previous one was answered or not -- so there
                    # should be another parameter to check, like
                    # "quota_achieved or one_unanswered_question_in_children_already_exists"
                    if (child_known_to_section and
                            (quota_achieved(child_id) or
                             one_unanswered_question_in_children_already_exists(child_id))):
                        break
                    if not child_known_to_section:
                        break
            else:
                raise StopIteration()  # no more children
            return IdList(child_ids,
                          runtime=self.my_osid_object._runtime,
                          proxy=self.my_osid_object._runtime)
        raise IllegalState()

    def get_children(self):
        """return the current child parts of this assessment part"""
        part_list = []
        for child_id in self.get_child_ids():
            part = get_part_from_magic_part_lookup_session(self._assessment_section,
                                                           child_id,
                                                           runtime=self.my_osid_object._runtime,
                                                           proxy=self.my_osid_object._proxy)
            # The magic_part_lookup_session will call part.initialize()
            part_list.append(part)
        return AssessmentPartList(part_list,
                                  runtime=self.my_osid_object._runtime,
                                  proxy=self.my_osid_object._proxy)

    def has_item_ids(self):
        """does this part have any item ids associated with it"""
        return bool(self.my_osid_object._my_map['itemIds'])

    def get_item_ids(self):
        """get item ids associated with this assessment part"""
        if self.has_item_ids():
            return IdList(self.my_osid_object._my_map['itemIds'],
                          runtime=self.my_osid_object._runtime,
                          proxy=self.my_osid_object._proxy)
        raise IllegalState()

    def get_learning_objective_ids(self):
        """gets all LO ids associated with this assessment part (should be only one for now)"""
        return IdList(self.my_osid_object._my_map['learningObjectiveIds'],
                      runtime=self.my_osid_object._runtime,
                      proxy=self.my_osid_object._proxy)

    learning_objective_ids = property(fget=get_learning_objective_ids)

    def has_waypoint_quota(self):
        """is a quoata on the number of required correct waypoint answers available"""
        return bool(self.my_osid_object._my_map['waypointQuota'])

    def get_waypoint_quota(self):
        """get the correct answer quota for this waypoint"""
        return self.my_osid_object._my_map['waypointQuota']

    waypoint_quota = property(fget=get_waypoint_quota)

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

    def has_parent_part(self):
        if self._magic_parent_id is None:
            # let my_osid_object handle it
            return bool(self.my_osid_object._my_map['assessmentPartId'])
        return True

    def get_assessment_part_id(self):
        if self._magic_parent_id is None:
            return Id(self.my_osid_object._my_map['assessmentPartId'])
            # raise AttributeError() # let my_osid_object handle it
        return self._magic_parent_id


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
            'default_cardinal_values': [None],
            'syntax': 'CARDINAL',
            'minimum_cardinal': 0,
            'maximum_cardinal': None,
            'cardinal_set': []
        }
        self._waypoint_quota_metadata = {
            'element_id': Id(self.my_osid_object_form._authority,
                             self.my_osid_object_form._namespace,
                             'waypoint-quota'),
            'element_label': 'Waypoint Quota',
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
        self.my_osid_object_form._my_map['waypointQuota'] = \
            self._waypoint_quota_metadata['default_cardinal_values'][0]
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
        if not self.my_osid_object_form._is_valid_cardinal(max_waypoint_items,
                                                           self.get_max_waypoint_items_metadata()):
            raise InvalidArgument()
        self.my_osid_object_form._my_map['maxWaypointItems'] = max_waypoint_items

    def clear_max_waypoint_items(self):
        if (self.get_max_waypoint_items_metadata().is_read_only() or
                self.get_max_waypoint_items_metadata().is_required()):
            raise NoAccess()
        self.my_osid_object_form._my_map['maxWaypointItems'] = \
            self.get_max_waypoint_items_metadata().get_default_cardinal_values()[0]

    def get_waypoint_quota_metadata(self):
        """get the metadata for waypoint quota"""
        metadata = dict(self._waypoint_quota_metadata)
        metadata.update({'existing_cardinal_values': self.my_osid_object_form._my_map['waypointQuota']})
        return Metadata(**metadata)

    def set_waypoint_quota(self, waypoint_quota):
        """how many waypoint questions need to be answered correctly"""
        if self.get_waypoint_quota_metadata().is_read_only():
            raise NoAccess()
        if not self.my_osid_object_form._is_valid_cardinal(waypoint_quota,
                                                           self.get_waypoint_quota_metadata()):
            raise InvalidArgument()
        self.my_osid_object_form._my_map['waypointQuota'] = waypoint_quota

    def clear_waypoint_quota(self):
        if (self.get_waypoint_quota_metadata().is_read_only() or
                self.get_waypoint_quota_metadata().is_required()):
            raise NoAccess()
        self.my_osid_object_form._my_map['waypointQuota'] = \
            self.get_waypoint_quota_metadata().get_default_cardinal_values()[0]

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
        if authority == MAGIC_PART_AUTHORITY:
            magic_identifier = unquote(assessment_part_id.identifier)
            orig_identifier = magic_identifier.split('?')[0]
            assessment_part = super(MagicAssessmentPartLookupSession, self).get_assessment_part(assessment_part_id=Id(authority=self._catalog.ident.authority,
                                                                                                                      namespace=assessment_part_id.get_identifier_namespace(),
                                                                                                                      identifier=orig_identifier))
            # should a magic assessment part's parent be the original part?
            # Or that original part's parent?
            assessment_part.initialize(assessment_part_id.identifier, self._my_assessment_section)
            return assessment_part
        else:
            if assessment_part_id.identifier == 'None':
                import pdb
                pdb.set_trace()
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
