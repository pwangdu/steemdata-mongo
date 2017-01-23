#!/usr/bin/python
# -*- coding: utf-8 -*-

import pymongo
from pymongo.errors import ConnectionFailure

MONGO_HOST = 'localhost'
MONGO_PORT = 27017
DB_NAME = 'Steem'


class MongoStorage(object):
    def __init__(self, db_name=DB_NAME, host=MONGO_HOST, port=MONGO_PORT):
        try:
            mongo_url = 'mongodb://%s:%s/%s' % (host, port, db_name)
            client = pymongo.MongoClient(mongo_url)
            self.db = client[db_name]

        except ConnectionFailure as e:
            print('Can not connect to MongoDB server: %s' % e)
            raise
        else:
            self.Accounts = self.db['Accounts']
            self.Posts = self.db['Posts']
            self.Operations = self.db['Operations']
            self.VirtualOperations = self.db['VirtualOperations']
            self.PriceHistory = self.db['PriceHistory']

    def list_collections(self):
        return self.db.collection_names()

    def reset_db(self):
        for col in self.list_collections():
            self.db.drop_collection(col)

    def ensure_indexes(self):
        self.Accounts.create_index('name', unique=True)
        self.Operations.create_index([('block_id', 1), ('type', 1), ('timestamp', 1)], unique=True)
        self.Operations.create_index([('type', 1)])

        self.VirtualOperations.create_index([('index', 1), ('account', 1), ('type', 1), ('timestamp', 1)], unique=True)
        self.VirtualOperations.create_index([('account', 1)])
        self.VirtualOperations.create_index([('type', 1)])

        self.Posts.create_index([('identifier', 1)], unique=True)
        self.Posts.create_index([('author', 1)])
        self.Posts.create_index([('body', 'text')], background=True)


class Settings(object):
    def __init__(self, mongo):
        self._settings = mongo.db['settings']
        self.settings = self._settings.find_one()

        if not self.settings:
            self._settings.insert_one(
                {"last_block": 1},
                {"account_index": 1},
                {"virtual_op_index": 1},
            )
            self.settings = self._settings.find_one()

    def last_block(self):
        return self.settings.get('last_block', 1)

    def update_last_block(self, block_num):
        return self._settings.update_one({}, {"$set": {'last_block': block_num}})

    def account_checkpoint(self):
        return self.settings.get('account_checkpoint', 1)

    def set_account_checkpoint(self, index_num):
        return self._settings.update_one({}, {"$set": {'account_checkpoint': index_num}})

    def virtual_op_checkpoint(self):
        return self.settings.get('virtual_op_checkpoint', 1)

    def set_virtual_op_checkpoint(self, username):
        return self._settings.update_one({}, {"$set": {'virtual_op_checkpoint': username}})


class Stats(object):
    def __init__(self, mongo):
        self.mongo = mongo
        self._stats = mongo.db['stats']
        self.stats = self._stats.find_one()

    def refresh(self):
        return self._stats.update({}, self._compile_stats(), upsert=True)

    def _compile_stats(self):
        # pprint(self.mongo.db.command('collstats', 'Accounts'))
        return {
            **{k: {
                'count': self.mongo.db[k].find().count(),
                'size': self.mongo.db.command('collstats', k).get('storageSize', 1) / 1e6,
            }
               for k in self.mongo.list_collections()},
            'dbSize': self.mongo.db.command('dbstats', 1000).get('storageSize', 1) / 1e6
        }


if __name__ == '__main__':
    mongo = MongoStorage()
    Stats(mongo).refresh()