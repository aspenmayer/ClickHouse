import pytest

from helpers.cluster import ClickHouseCluster

uuids = []


@pytest.fixture(scope="module")
def cluster():
    try:
        cluster = ClickHouseCluster(__file__)
        cluster.add_instance(
            "node1", main_configs=["configs/storage_conf.xml"], with_nginx=True
        )
        cluster.add_instance(
            "node2", main_configs=["configs/storage_conf_web.xml"], with_nginx=True
        )
        cluster.add_instance(
            "node3", main_configs=["configs/storage_conf_web.xml"], with_nginx=True
        )
        cluster.start()

        node1 = cluster.instances["node1"]
        expected = ""
        global uuids
        for i in range(3):
            node1.query(
                f"CREATE TABLE data{i} (id Int32) ENGINE = MergeTree() ORDER BY id SETTINGS storage_policy = 'def', min_bytes_for_wide_part=1;"
            )

            for _ in range(10):
                node1.query(
                    f"INSERT INTO data{i} SELECT number FROM numbers(500000 * {i+1})"
                )
            expected = node1.query(f"SELECT * FROM data{i} ORDER BY id")

            metadata_path = node1.query(
                f"SELECT data_paths FROM system.tables WHERE name='data{i}'"
            )
            metadata_path = metadata_path[
                metadata_path.find("/") : metadata_path.rfind("/") + 1
            ]
            print(f"Metadata: {metadata_path}")

            node1.exec_in_container(
                [
                    "bash",
                    "-c",
                    "/usr/bin/clickhouse static-files-disk-uploader --test-mode --url http://nginx:80/test1 --metadata-path {}".format(
                        metadata_path
                    ),
                ],
                user="root",
            )
            parts = metadata_path.split("/")
            uuids.append(parts[3])
            print(f"UUID: {parts[3]}")

        yield cluster

    finally:
        cluster.shutdown()


@pytest.mark.parametrize("node_name", ["node2"])
def test_usage(cluster, node_name):
    node1 = cluster.instances["node1"]
    node2 = cluster.instances[node_name]
    global uuids
    assert len(uuids) == 3
    for i in range(3):
        node2.query(
            """
            ATTACH TABLE test{} UUID '{}'
            (id Int32) ENGINE = MergeTree() ORDER BY id
            SETTINGS storage_policy = 'web';
        """.format(
                i, uuids[i], i, i
            )
        )

        result = node2.query("SELECT * FROM test{} settings max_threads=20".format(i))

        result = node2.query("SELECT count() FROM test{}".format(i))
        assert int(result) == 5000000 * (i + 1)

        result = node2.query(
            "SELECT id FROM test{} WHERE id % 56 = 3 ORDER BY id".format(i)
        )
        assert result == node1.query(
            "SELECT id FROM data{} WHERE id % 56 = 3 ORDER BY id".format(i)
        )

        result = node2.query(
            "SELECT id FROM test{} WHERE id > 789999 AND id < 999999 ORDER BY id".format(
                i
            )
        )
        assert result == node1.query(
            "SELECT id FROM data{} WHERE id > 789999 AND id < 999999 ORDER BY id".format(
                i
            )
        )

        node2.query("DROP TABLE test{} SYNC".format(i))
        print(f"Ok {i}")


def test_incorrect_usage(cluster):
    node1 = cluster.instances["node1"]
    node2 = cluster.instances["node3"]
    global uuids
    node2.query(
        """
        ATTACH TABLE test0 UUID '{}'
        (id Int32) ENGINE = MergeTree() ORDER BY id
        SETTINGS storage_policy = 'web';
    """.format(
            uuids[0]
        )
    )

    result = node2.query("SELECT count() FROM test0")
    assert int(result) == 5000000

    result = node2.query_and_get_error("ALTER TABLE test0 ADD COLUMN col1 Int32 first")
    assert "Table is read-only" in result

    result = node2.query_and_get_error("TRUNCATE TABLE test0")
    assert "Table is read-only" in result

    result = node2.query_and_get_error("OPTIMIZE TABLE test0 FINAL")
    assert "Only read-only operations are supported" in result

    node2.query("DROP TABLE test0 SYNC")


@pytest.mark.parametrize("node_name", ["node2"])
def test_cache(cluster, node_name):
    node1 = cluster.instances["node1"]
    node2 = cluster.instances[node_name]
    global uuids
    assert len(uuids) == 3
    for i in range(3):
        node2.query(
            """
            ATTACH TABLE test{} UUID '{}'
            (id Int32) ENGINE = MergeTree() ORDER BY id
            SETTINGS storage_policy = 'cached_web';
        """.format(
                i, uuids[i], i, i
            )
        )

        result = node2.query(
            """
            SYSTEM DROP FILESYSTEM CACHE;
            SELECT count() FROM system.filesystem_cache;
        """
        )
        assert int(result) == 0

        result = node2.query("SELECT * FROM test{} settings max_threads=20".format(i))

        result = node2.query(
            """
            SELECT count() FROM system.filesystem_cache;
        """
        )
        assert int(result) > 0

        result = node2.query("SELECT count() FROM test{}".format(i))
        assert int(result) == 5000000 * (i + 1)

        result = node2.query(
            "SELECT id FROM test{} WHERE id % 56 = 3 ORDER BY id".format(i)
        )
        assert result == node1.query(
            "SELECT id FROM data{} WHERE id % 56 = 3 ORDER BY id".format(i)
        )

        result = node2.query(
            "SELECT id FROM test{} WHERE id > 789999 AND id < 999999 ORDER BY id".format(
                i
            )
        )
        assert result == node1.query(
            "SELECT id FROM data{} WHERE id > 789999 AND id < 999999 ORDER BY id".format(
                i
            )
        )

        node2.query("DROP TABLE test{} SYNC".format(i))
        print(f"Ok {i}")
