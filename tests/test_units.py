import unittest
from lab.metrics import SQL_SUMMARY_FIELDS, SQL_SUMMARY_FIELD_UNITS, ITEM_METADATA_TAGS

class SnapshotUnitContract(unittest.TestCase):
    def test_all_selected_mysql_timer_fields_have_picosecond_metadata(self):
        timer_fields={field for field in SQL_SUMMARY_FIELDS if 'TIMER_' in field}
        self.assertEqual(set(SQL_SUMMARY_FIELD_UNITS),timer_fields)
        tags={tag['tag']:tag['value'] for tag in ITEM_METADATA_TAGS['sl.mysql.sql_summary']}
        for field in timer_fields:
            self.assertEqual(tags['sl.field_unit.'+field],'ps')
        self.assertEqual(tags['sl.snapshot_scope'],'server_lifetime')

if __name__=='__main__': unittest.main()
