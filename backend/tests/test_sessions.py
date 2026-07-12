import sessions


def test_create_database_session_computes_record_count_from_table_index():
    table_index = [{"table_name": "orders", "row_estimate": 500}, {"table_name": "customers", "row_estimate": 50}]
    s = sessions.create(source_type="database", conn_string="postgresql://x",
                         table_index=table_index, dataset_name="my-db", data_source_id=3)
    assert s.source_type == "database"
    assert s.table_index == table_index
    assert s.data_source_id == 3
    assert s.record_count == 550
    assert s.dataset_name == "my-db"
    assert sessions.get(s.session_id) is s


def test_create_inflectiv_session_unaffected_by_table_index_default():
    s = sessions.create(global_key="k", dataset={"id": 1, "name": "ds", "knowledge_source_count": 12})
    assert s.table_index == []
    assert s.record_count == 12
