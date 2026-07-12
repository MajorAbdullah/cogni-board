from db_connector import _assemble_table_index


def test_assemble_table_index_groups_columns_and_fks():
    col_rows = [
        {"table_name": "orders", "column_name": "id", "data_type": "integer", "row_estimate": 100},
        {"table_name": "orders", "column_name": "customer_id", "data_type": "integer", "row_estimate": 100},
        {"table_name": "customers", "column_name": "id", "data_type": "integer", "row_estimate": 20},
    ]
    fk_rows = [
        {"table_name": "orders", "column_name": "customer_id", "ref_table": "customers", "ref_column": "id"},
    ]
    result = _assemble_table_index(col_rows, fk_rows)

    assert [t["table_name"] for t in result] == ["customers", "orders"]
    orders = result[1]
    assert orders["row_estimate"] == 100
    assert orders["columns"] == [{"name": "id", "type": "integer"}, {"name": "customer_id", "type": "integer"}]
    assert orders["foreign_keys"] == [{"column": "customer_id", "ref_table": "customers", "ref_column": "id"}]
    assert result[0]["foreign_keys"] == []


def test_assemble_table_index_handles_null_row_estimate():
    col_rows = [{"table_name": "t", "column_name": "id", "data_type": "integer", "row_estimate": None}]
    result = _assemble_table_index(col_rows, [])
    assert result[0]["row_estimate"] == 0
