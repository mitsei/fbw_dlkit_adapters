import json

from bson import ObjectId

from dlkit.mongo.assessment.objects import Item, Question
from dlkit.mongo.assessment.sessions import ItemLookupSession
from dlkit.mongo.utilities import MongoClientValidated

from urllib import unquote


class RandomizedMCItemLookupSession(ItemLookupSession):
    """this session does "magic" unscrambling of MC question items with
        unique IDs, where the choice order has been specified in the ID.

        For example, we want MC questions to be randomized when they
        are given to the students, so each student sees the choices in
        a different order.

        Student 1:

        Q) What is X?

        a) choice 1
        b) choice 0
        c) choice 3
        d) choice 2

        Student 2:

        Q) What is X?

        a) choice 2
        b) choice 1
        c) choice 0
        d) choice 3

        But in many situations, when the student views the question again
        (i.e. they don't answer and come back, they answer but want to see
        their history, etc.), we want to record the original ordering
        of choices, to reduce confusion. This is being preserved
        in a "magic" ID for the question, which captures the
        state / parameters of the question. This ID is then stored in the
        AssessmentTaken record for that student.

        This "magic" adapter session plugs into the AssessmentSession
        and the AssessmentResultsSession and looks for any question ID
        that is flagged as a Randomized MC Question. It then knows
        to set the choice order to match the previous state. All other
        items are passed along to the unaltered MongoDB ItemLookupSession.

        This adapter session has out-of-band knowledge of the authority
        of the items it needs to deconstruct -- i.e. from the DLKit
        records implementation.
    """

    def get_item(self, item_id):
        if item_id.authority == 'magic-randomize-choices-question-record':
            # for now, this will not work with aliased IDs...
            original_identifier = unquote(item_id.identifier).split('?')[0]
            collection = MongoClientValidated('assessment',
                                              collection='Item',
                                              runtime=self._runtime)
            result = collection.find_one(
                dict({'_id': ObjectId(original_identifier)},
                     **self._view_filter()))

            # inject this back in so that get_question() can extract the choices
            result['_id'] = item_id.identifier
            return RandomizedMCItem(osid_object_map=result,
                                    runtime=self._runtime,
                                    proxy=self._proxy)
        else:
            return super(RandomizedMCItemLookupSession, self).get_item(item_id)


class RandomizedMCItem(Item):
    def get_question(self):
        parameters = json.loads(unquote(self.ident.identifier).split('?')[0])
        choice_ids = parameters['choiceIds']
        configurable_question = Question(osid_object_map=self._my_map['question'],
                                         runtime=self._runtime)
        configurable_question.set_choice_ids(choice_ids=choice_ids)
        return configurable_question

    question = property(fget=get_question)
