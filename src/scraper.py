import time
from contextlib import suppress

from funcy.seqs import flatten
from pymongo.errors import DuplicateKeyError
from steem import Steem
from steem.blockchain import Blockchain
from steemdata.helpers import timeit
from steemdata.utils import json_expand, typify
from toolz import merge_with

from methods import update_account, update_account_ops, parse_operation, upsert_comment, delete_comment
from mongostorage import MongoStorage, Settings, Stats
from tasks import batch_update_async, update_comment_async
from utils import fetch_price_feed, get_usernames_batch


def scrape_all_users(mongo):
    """Scrape all existing users and insert/update their entries in Accounts collection."""
    steem = Steem()
    s = Settings(mongo)

    account_checkpoint = s.account_checkpoint()
    if account_checkpoint:
        usernames = list(get_usernames_batch(account_checkpoint, steem))
    else:
        usernames = list(get_usernames_batch(steem))

    for username in usernames:
        update_account(mongo, username, load_extras=True)
        update_account_ops(mongo, username)
        s.set_account_checkpoint(username)
        print('Updated @%s' % username)

    # this was the last batch
    if account_checkpoint and len(usernames) < 1000:
        s.set_account_checkpoint(-1)


def scrape_operations(mongo):
    """Fetch all operations from last known block forward."""
    settings = Settings(mongo)
    blockchain = Blockchain(mode="irreversible")
    last_block = settings.last_block()

    # handle batching
    _batch_size = 50
    batch_dicts = []

    history = blockchain.history(
        start_block=last_block - _batch_size * 2,
    )

    def custom_merge(*args):
        return list(set(filter(bool, flatten(args))))

    def schedule_batch(_batch_dicts):
        """Send a batch to background worker, and reset _dicts container"""
        _batch = merge_with(custom_merge, *_batch_dicts)
        if _batch:
            batch_update_async.delay(_batch)

    print('\n> Fetching operations, starting with block %d...' % last_block)
    for operation in history:
        # handle comments
        if operation['type'] in ['comment', 'delete_comment']:
            post_identifier = "@%s/%s" % (operation['author'], operation['permlink'])
            if operation['type'] == 'delete_comment':
                delete_comment(mongo, post_identifier)
            else:
                update_comment_async.delay(post_identifier, recursive=True)

        # if we're close to blockchain head, enable batching
        if last_block > blockchain.get_current_block_num() - _batch_size * 5:
            batch_dicts.append(parse_operation(operation))

        # insert operation
        with suppress(DuplicateKeyError):
            mongo.Operations.insert_one(json_expand(typify(operation)))

        # if this is a new block, checkpoint it, and schedule batch processing
        if operation['block_num'] != last_block:
            last_block = operation['block_num']
            settings.update_last_block(last_block - 1)

            if last_block % 10 == 0:
                print("#%s: %s" % (last_block, time.ctime()))

            if last_block % _batch_size == 0:
                schedule_batch(batch_dicts)
                batch_dicts = []


def validate_operations(mongo):
    """ Scan each block in db and validate its operations for consistency reasons. """
    blockchain = Blockchain(mode="irreversible")
    highest_block = mongo.Operations.find_one({}, sort=[('block_num', -1)])['block_num']

    for block_num in range(highest_block, 1, -1):
        if block_num % 10 == 0:
            print('Validating block #%s' % block_num)
        block = list(blockchain.stream(start=block_num, stop=block_num))

        # remove all invalid or changed operations
        conditions = {'block_num': block_num, '_id': {'$nin': [x['_id'] for x in block]}}
        mongo.Operations.delete_many(conditions)

        # insert any missing operations
        for op in block:
            with suppress(DuplicateKeyError):
                mongo.Operations.insert_one(json_expand(typify(op)))

        # re-process comments
        for comment in (x for x in block if x['type'] == 'comment'):
            upsert_comment(mongo, '%s/%s' % (comment['author'], comment['permlink']))


def refresh_dbstats(mongo):
    while True:
        Stats(mongo).refresh()
        time.sleep(60)


def scrape_prices(mongo):
    """ Update PriceHistory every hour.
    """
    while True:
        prices = fetch_price_feed()
        mongo.PriceHistory.insert_one(prices)
        time.sleep(60 * 5)


def override(mongo):
    """Various fixes to avoid re-scraping"""
    # fix posts
    # broken_posts = mongo.Posts.find({'total_payout_value': {}}, {'identifier': 1}).limit(1000)
    # for identifier in pluck('identifier', broken_posts):
    #     upsert_post(mongo, identifier)
    #
    # # fix comments
    # broken_comments = mongo.Comments.find({'total_payout_value': {}}, {'identifier': 1}).limit(1000)
    # for identifier in pluck('identifier', broken_comments):
    #     upsert_comment(mongo, identifier)
    #
    # # fix custom_json
    # for op in mongo.Operations.find({'type': 'custom_json', 'json': {'$type': 'string'}}):
    #     if type(op['json']) != str:
    #         continue
    #     with suppress(TypeError):
    #         mongo.Operations.update(op, {'$set': {'json': json.loads(op['json'])}})
    # for op in mongo.AccountOperations.find({'type': 'custom_json', 'json': {'$type': 'string'}}):
    #     if type(op['json']) != str:
    #         continue
    #     with suppress(TypeError):
    #         mongo.AccountOperations.update(op, {'$set': {'json': json.loads(op['json'])}})

    # dont hog the loop
    time.sleep(600)


def run():
    m = MongoStorage()
    with timeit():
        # update_account(m, 'furion', load_extras=False)
        # m.ensure_indexes()
        # scrape_misc(m)
        # scrape_all_users(m, Steem())
        # validate_operations(m)
        # override(m)
        scrape_operations(m)
        # scrape_virtual_operations(m)
        # scrape_active_posts(m)


if __name__ == '__main__':
    run()
