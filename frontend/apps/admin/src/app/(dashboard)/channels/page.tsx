"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  App,
  Button,
  Card,
  Col,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import { CheckOutlined, EditOutlined, PlusOutlined, ReloadOutlined, SafetyCertificateOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api, { extractErrorMessage } from "@/lib/api";

const { Title, Text } = Typography;

type PageResult<T> = { items: T[]; total: number; page: number; page_size: number };
type Overview = {
  distributor_count: number;
  region_count: number;
  store_count: number;
  allocated_quantity: number;
  pending_diversion_count: number;
};
type Distributor = {
  id: string;
  name: string;
  code: string;
  contact_name?: string;
  status: string;
  region_count: number;
  store_count: number;
  allocated_quantity: number;
};
type Region = {
  id: string;
  name: string;
  code: string;
  city?: string;
  province?: string;
  status: string;
  distributor_id?: string;
  distributor_name?: string;
  store_count: number;
};
type Store = {
  id: string;
  name: string;
  code: string;
  address?: string;
  status: string;
  region_id?: string;
  region_name?: string;
  distributor_id?: string;
  distributor_name?: string;
  allocated_quantity: number;
};
type Allocation = {
  id: string;
  batch_id: string;
  batch_code?: string;
  product_name?: string;
  sku_name?: string;
  store_id?: string;
  store_name?: string;
  distributor_name?: string;
  region_name?: string;
  quantity: number;
  batch_quantity: number;
  remaining_quantity: number;
  allocated_at?: string;
};
type DiversionClue = {
  id: string;
  public_id: string;
  expected_region?: string;
  detected_city?: string;
  resolved: boolean;
  resolution_note?: string | null;
};
type Account = { id: string; name: string; email: string };
type AccountScope = {
  id: string;
  account_id: string;
  scope_type: "distributor" | "store";
  distributor_id?: string;
  store_id?: string;
};
type Batch = { id: string; batch_code: string; quantity: number; product_name?: string; sku_name?: string };

const emptyPage = <T,>(): PageResult<T> => ({ items: [], total: 0, page: 1, page_size: 20 });

function statusTag(status?: string) {
  if (status === "active") return <Tag color="green">启用</Tag>;
  if (status === "inactive") return <Tag color="default">停用</Tag>;
  return <Tag>{status || "未知"}</Tag>;
}

function batchLabel(batch: Batch) {
  return [batch.batch_code, batch.product_name, batch.sku_name].filter(Boolean).join(" / ");
}

export default function ChannelsPage() {
  const appApi = App.useApp();
  const messageRef = useRef(appApi.message);
  const modalRef = useRef(appApi.modal);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [distributors, setDistributors] = useState<PageResult<Distributor>>(emptyPage);
  const [regions, setRegions] = useState<PageResult<Region>>(emptyPage);
  const [stores, setStores] = useState<PageResult<Store>>(emptyPage);
  const [allocations, setAllocations] = useState<PageResult<Allocation>>(emptyPage);
  const [clues, setClues] = useState<PageResult<DiversionClue>>(emptyPage);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [scopes, setScopes] = useState<AccountScope[]>([]);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState("distributors");

  const [entityModal, setEntityModal] = useState<{ type: "distributor" | "region" | "store"; record?: Distributor | Region | Store } | null>(null);
  const [allocationOpen, setAllocationOpen] = useState(false);
  const [scopeOpen, setScopeOpen] = useState(false);
  const [currentClue, setCurrentClue] = useState<DiversionClue | null>(null);
  const [entityForm] = Form.useForm();
  const [allocationForm] = Form.useForm();
  const [scopeForm] = Form.useForm();
  const [resolveForm] = Form.useForm();

  useEffect(() => {
    messageRef.current = appApi.message;
    modalRef.current = appApi.modal;
  }, [appApi.message, appApi.modal]);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [overviewRes, distRes, regionRes, storeRes, allocRes, clueRes, batchRes, accountRes, scopeRes] = await Promise.all([
        api.get("/channels/overview"),
        api.get("/channels/distributors"),
        api.get("/channels/regions"),
        api.get("/channels/stores"),
        api.get("/channels/code-allocations"),
        api.get("/channels/diversion-clues"),
        api.get("/code-batches"),
        api.get("/accounts"),
        api.get("/channels/account-scopes"),
      ]);
      setOverview(overviewRes.data);
      setDistributors(distRes.data);
      setRegions(regionRes.data);
      setStores(storeRes.data);
      setAllocations(allocRes.data);
      setClues(clueRes.data);
      setBatches(batchRes.data?.items ? batchRes.data.items : []);
      setAccounts(Array.isArray(accountRes.data) ? accountRes.data : accountRes.data?.items || []);
      setScopes(Array.isArray(scopeRes.data) ? scopeRes.data : []);
    } catch (err) {
      messageRef.current.error(extractErrorMessage(err, "加载渠道数据失败"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const distributorOptions = useMemo(
    () => distributors.items.map((item) => ({ label: `${item.name} (${item.code})`, value: item.id })),
    [distributors.items],
  );
  const regionOptions = useMemo(
    () => regions.items.map((item) => ({ label: `${item.name}${item.city ? ` / ${item.city}` : ""}`, value: item.id })),
    [regions.items],
  );
  const storeOptions = useMemo(
    () => stores.items.map((item) => ({ label: `${item.name} (${item.code})`, value: item.id })),
    [stores.items],
  );

  const openEntityModal = (type: "distributor" | "region" | "store", record?: Distributor | Region | Store) => {
    setEntityModal({ type, record });
    entityForm.setFieldsValue(record || {});
  };

  const closeEntityModal = () => {
    setEntityModal(null);
    entityForm.resetFields();
  };

  const saveEntity = async (values: Record<string, unknown>) => {
    if (!entityModal) return;
    const paths = {
      distributor: "/channels/distributors",
      region: "/channels/regions",
      store: "/channels/stores",
    };
    try {
      if (entityModal.record) {
        await api.patch(`${paths[entityModal.type]}/${entityModal.record.id}`, values);
        messageRef.current.success("资料已更新");
      } else {
        await api.post(paths[entityModal.type], values);
        messageRef.current.success("资料已创建");
      }
      closeEntityModal();
      loadData();
    } catch (err) {
      messageRef.current.error(extractErrorMessage(err, "保存失败"));
    }
  };

  const toggleStatus = (type: "distributor" | "region" | "store", record: Distributor | Region | Store) => {
    const next = record.status === "active" ? "inactive" : "active";
    modalRef.current.confirm({
      title: next === "active" ? "启用该记录？" : "停用该记录？",
      content: next === "active" ? "启用后可继续用于渠道分配和统计。" : "停用后不会出现在默认启用列表中。",
      onOk: async () => {
        const paths = {
          distributor: "/channels/distributors",
          region: "/channels/regions",
          store: "/channels/stores",
        };
        await api.patch(`${paths[type]}/${record.id}`, { status: next });
        messageRef.current.success(next === "active" ? "已启用" : "已停用");
        loadData();
      },
    });
  };

  const createAllocation = async (values: Record<string, unknown>) => {
    try {
      await api.post("/channels/code-allocations", values);
      messageRef.current.success("码段已分配");
      setAllocationOpen(false);
      allocationForm.resetFields();
      loadData();
    } catch (err) {
      messageRef.current.error(extractErrorMessage(err, "分配失败"));
    }
  };

  const createScope = async (values: Record<string, unknown>) => {
    try {
      await api.post("/channels/account-scopes", values);
      messageRef.current.success("账号范围已绑定");
      setScopeOpen(false);
      scopeForm.resetFields();
      loadData();
    } catch (err) {
      messageRef.current.error(extractErrorMessage(err, "绑定失败"));
    }
  };

  const resolveClue = async (values: { resolution_note?: string }) => {
    if (!currentClue) return;
    try {
      await api.put(`/risk-dashboard/diversion-clues/${currentClue.id}/resolve`, {
        resolution_note: values.resolution_note || "",
      });
      messageRef.current.success("线索已处理");
      setCurrentClue(null);
      resolveForm.resetFields();
      loadData();
    } catch (err) {
      messageRef.current.error(extractErrorMessage(err, "处理失败"));
    }
  };

  const distributorColumns: ColumnsType<Distributor> = [
    {
      title: "经销商",
      dataIndex: "name",
      render: (_, record) => (
        <Space orientation="vertical" size={0}>
          <Text strong>{record.name}</Text>
          <Text type="secondary">{record.code}</Text>
        </Space>
      ),
    },
    { title: "组织规模", render: (_, record) => `${record.region_count} 个区域 / ${record.store_count} 个门店` },
    { title: "已分配码量", render: (_, record) => `${record.allocated_quantity} 个码` },
    { title: "状态", dataIndex: "status", render: statusTag },
    {
      title: "操作",
      render: (_, record) => (
        <Space>
          <Button size="small" type="link" icon={<EditOutlined />} onClick={() => openEntityModal("distributor", record)}>
            编辑
          </Button>
          <Button size="small" type="link" onClick={() => toggleStatus("distributor", record)}>
            {record.status === "active" ? "停用" : "启用"}
          </Button>
        </Space>
      ),
    },
  ];

  const regionColumns: ColumnsType<Region> = [
    { title: "区域", dataIndex: "name" },
    { title: "城市", dataIndex: "city", render: (value) => value || "未设置" },
    { title: "经销商", dataIndex: "distributor_name", render: (value) => value || "未绑定" },
    { title: "门店数", dataIndex: "store_count", render: (value) => `${value || 0} 个` },
    { title: "状态", dataIndex: "status", render: statusTag },
    {
      title: "操作",
      render: (_, record) => (
        <Space>
          <Button size="small" type="link" icon={<EditOutlined />} onClick={() => openEntityModal("region", record)}>
            编辑
          </Button>
          <Button size="small" type="link" onClick={() => toggleStatus("region", record)}>
            {record.status === "active" ? "停用" : "启用"}
          </Button>
        </Space>
      ),
    },
  ];

  const storeColumns: ColumnsType<Store> = [
    { title: "门店", dataIndex: "name" },
    { title: "区域", dataIndex: "region_name", render: (value) => value || "未绑定" },
    { title: "经销商", dataIndex: "distributor_name", render: (value) => value || "未绑定" },
    { title: "已分配码量", dataIndex: "allocated_quantity", render: (value) => `${value || 0} 个码` },
    { title: "状态", dataIndex: "status", render: statusTag },
    {
      title: "操作",
      render: (_, record) => (
        <Space>
          <Button size="small" type="link" icon={<EditOutlined />} onClick={() => openEntityModal("store", record)}>
            编辑
          </Button>
          <Button size="small" type="link" onClick={() => toggleStatus("store", record)}>
            {record.status === "active" ? "停用" : "启用"}
          </Button>
        </Space>
      ),
    },
  ];

  const allocationColumns: ColumnsType<Allocation> = [
    { title: "码批次", dataIndex: "batch_code" },
    {
      title: "产品/SKU",
      render: (_, record) => [record.product_name || "未命名产品", record.sku_name].filter(Boolean).join(" / "),
    },
    { title: "门店", dataIndex: "store_name", render: (value) => value || "未绑定门店" },
    { title: "经销商", dataIndex: "distributor_name", render: (value) => value || "未绑定经销商" },
    { title: "分配数量", dataIndex: "quantity", render: (value) => `${value || 0} 个` },
    { title: "剩余", dataIndex: "remaining_quantity", render: (value) => `剩余 ${value || 0}` },
  ];

  const clueColumns: ColumnsType<DiversionClue> = [
    { title: "码", dataIndex: "public_id" },
    { title: "预期区域", dataIndex: "expected_region", render: (value) => value || "未设置" },
    { title: "实际城市", dataIndex: "detected_city", render: (value) => value || "未知" },
    { title: "状态", dataIndex: "resolved", render: (value) => (value ? <Tag color="green">已处理</Tag> : <Tag color="red">待处理</Tag>) },
    {
      title: "操作",
      render: (_, record) =>
        record.resolved ? (
          <Button size="small" type="link" onClick={() => setCurrentClue(record)}>
            查看
          </Button>
        ) : (
          <Button size="small" type="link" icon={<CheckOutlined />} onClick={() => setCurrentClue(record)}>
            处理
          </Button>
        ),
    },
  ];

  const scopeColumns: ColumnsType<AccountScope> = [
    {
      title: "账号",
      dataIndex: "account_id",
      render: (value) => {
        const account = accounts.find((item) => item.id === value);
        return account ? `${account.name} / ${account.email}` : value;
      },
    },
    { title: "入口类型", dataIndex: "scope_type", render: (value) => (value === "distributor" ? "经销商入口" : "门店入口") },
    {
      title: "绑定范围",
      render: (_, record) => {
        if (record.scope_type === "distributor") {
          return distributors.items.find((item) => item.id === record.distributor_id)?.name || record.distributor_id;
        }
        return stores.items.find((item) => item.id === record.store_id)?.name || record.store_id;
      },
    },
  ];

  const entityTitle = entityModal?.type === "distributor" ? "经销商" : entityModal?.type === "region" ? "区域" : "门店";

  return (
    <div>
      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <Title level={4} className="!mb-1">
            渠道管理
          </Title>
          <Text type="secondary">维护渠道组织、分配码段，并跟进跨区扫码线索。</Text>
        </div>
        <Button icon={<ReloadOutlined />} onClick={loadData}>
          刷新
        </Button>
      </div>

      <Row gutter={[16, 16]} className="mb-5">
        <Col xs={12} md={8} lg={4}>
          <Card size="small">
            <Statistic title="经销商" value={overview?.distributor_count || 0} />
          </Card>
        </Col>
        <Col xs={12} md={8} lg={4}>
          <Card size="small">
            <Statistic title="区域" value={overview?.region_count || 0} />
          </Card>
        </Col>
        <Col xs={12} md={8} lg={4}>
          <Card size="small">
            <Statistic title="门店" value={overview?.store_count || 0} />
          </Card>
        </Col>
        <Col xs={12} md={8} lg={4}>
          <Card size="small">
            <Statistic title="已分配码量" value={overview?.allocated_quantity || 0} />
          </Card>
        </Col>
        <Col xs={12} md={8} lg={4}>
          <Card size="small">
            <Statistic title="待处理线索" value={overview?.pending_diversion_count || 0} />
          </Card>
        </Col>
      </Row>

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: "distributors",
            label: "经销商",
            children: (
              <>
                <div className="mb-4 flex justify-end">
                  <Button type="primary" icon={<PlusOutlined />} onClick={() => openEntityModal("distributor")}>
                    新建经销商
                  </Button>
                </div>
                <Table columns={distributorColumns} dataSource={distributors.items} rowKey="id" loading={loading} locale={{ emptyText: <Empty description="先新建经销商，再绑定区域和门店" /> }} />
              </>
            ),
          },
          {
            key: "regions",
            label: "区域",
            children: (
              <>
                <div className="mb-4 flex justify-end">
                  <Button type="primary" icon={<PlusOutlined />} onClick={() => openEntityModal("region")}>
                    新建区域
                  </Button>
                </div>
                <Table columns={regionColumns} dataSource={regions.items} rowKey="id" loading={loading} />
              </>
            ),
          },
          {
            key: "stores",
            label: "门店",
            children: (
              <>
                <div className="mb-4 flex justify-end">
                  <Button type="primary" icon={<PlusOutlined />} onClick={() => openEntityModal("store")}>
                    新建门店
                  </Button>
                </div>
                <Table columns={storeColumns} dataSource={stores.items} rowKey="id" loading={loading} />
              </>
            ),
          },
          {
            key: "assign",
            label: "码段分配",
            children: (
              <>
                <div className="mb-4 flex justify-end">
                  <Button type="primary" icon={<PlusOutlined />} onClick={() => setAllocationOpen(true)}>
                    新建分配
                  </Button>
                </div>
                <Table columns={allocationColumns} dataSource={allocations.items} rowKey="id" loading={loading} />
              </>
            ),
          },
          {
            key: "diversion",
            label: "窜货线索",
            children: <Table columns={clueColumns} dataSource={clues.items} rowKey="id" loading={loading} />,
          },
          {
            key: "scopes",
            label: "账号授权",
            children: (
              <>
                <div className="mb-4 flex justify-end">
                  <Button type="primary" icon={<SafetyCertificateOutlined />} onClick={() => setScopeOpen(true)}>
                    绑定入口账号
                  </Button>
                </div>
                <Table columns={scopeColumns} dataSource={scopes} rowKey="id" loading={loading} />
              </>
            ),
          },
        ]}
      />

      <Modal
        title={`${entityModal?.record ? "编辑" : "新建"}${entityTitle || ""}`}
        open={!!entityModal}
        onCancel={closeEntityModal}
        onOk={() => entityForm.submit()}
        destroyOnHidden
      >
        <Form form={entityForm} layout="vertical" onFinish={saveEntity}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: "请输入名称" }]}>
            <Input />
          </Form.Item>
          {entityModal?.type !== "store" && (
            <Form.Item name="code" label="编码" rules={[{ required: !entityModal?.record, message: "请输入编码" }]}>
              <Input disabled={!!entityModal?.record} />
            </Form.Item>
          )}
          {entityModal?.type === "store" && (
            <Form.Item name="code" label="编码" rules={[{ required: !entityModal?.record, message: "请输入编码" }]}>
              <Input disabled={!!entityModal?.record} />
            </Form.Item>
          )}
          {entityModal?.type === "distributor" && (
            <Form.Item name="contact_name" label="联系人">
              <Input />
            </Form.Item>
          )}
          {entityModal?.type === "region" && (
            <>
              <Form.Item name="province" label="省份">
                <Input />
              </Form.Item>
              <Form.Item name="city" label="城市">
                <Input />
              </Form.Item>
              <Form.Item name="distributor_id" label="所属经销商">
                <Select allowClear options={distributorOptions} />
              </Form.Item>
            </>
          )}
          {entityModal?.type === "store" && (
            <>
              <Form.Item name="region_id" label="所属区域">
                <Select allowClear options={regionOptions} />
              </Form.Item>
              <Form.Item name="distributor_id" label="所属经销商">
                <Select allowClear options={distributorOptions} />
              </Form.Item>
              <Form.Item name="address" label="地址">
                <Input />
              </Form.Item>
            </>
          )}
        </Form>
      </Modal>

      <Modal title="新建码段分配" open={allocationOpen} onCancel={() => setAllocationOpen(false)} onOk={() => allocationForm.submit()} destroyOnHidden>
        <Form form={allocationForm} layout="vertical" onFinish={createAllocation}>
          <Form.Item name="batch_id" label="码批次" rules={[{ required: true, message: "请选择码批次" }]}>
            <Select options={batches.map((batch) => ({ label: batchLabel(batch), value: batch.id }))} />
          </Form.Item>
          <Form.Item name="store_id" label="门店" rules={[{ required: true, message: "请选择门店" }]}>
            <Select options={storeOptions} />
          </Form.Item>
          <Form.Item name="quantity" label="分配数量" rules={[{ required: true, message: "请输入分配数量" }]}>
            <InputNumber min={1} className="w-full" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title="绑定入口账号" open={scopeOpen} onCancel={() => setScopeOpen(false)} onOk={() => scopeForm.submit()} destroyOnHidden>
        <Form form={scopeForm} layout="vertical" onFinish={createScope}>
          <Form.Item name="account_id" label="账号" rules={[{ required: true, message: "请选择账号" }]}>
            <Select options={accounts.map((account) => ({ label: `${account.name} / ${account.email}`, value: account.id }))} />
          </Form.Item>
          <Form.Item name="scope_type" label="入口类型" rules={[{ required: true, message: "请选择入口类型" }]}>
            <Select
              options={[
                { label: "经销商入口", value: "distributor" },
                { label: "门店入口", value: "store" },
              ]}
            />
          </Form.Item>
          <Form.Item noStyle shouldUpdate>
            {({ getFieldValue }) =>
              getFieldValue("scope_type") === "store" ? (
                <Form.Item name="store_id" label="门店" rules={[{ required: true, message: "请选择门店" }]}>
                  <Select options={storeOptions} />
                </Form.Item>
              ) : (
                <Form.Item name="distributor_id" label="经销商" rules={[{ required: true, message: "请选择经销商" }]}>
                  <Select options={distributorOptions} />
                </Form.Item>
              )
            }
          </Form.Item>
        </Form>
      </Modal>

      <Drawer title="窜货线索处理" open={!!currentClue} onClose={() => setCurrentClue(null)} size="large">
        {currentClue && (
          <Space orientation="vertical" className="w-full" size={16}>
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="码">{currentClue.public_id}</Descriptions.Item>
              <Descriptions.Item label="预期区域">{currentClue.expected_region || "未设置"}</Descriptions.Item>
              <Descriptions.Item label="实际城市">{currentClue.detected_city || "未知"}</Descriptions.Item>
              <Descriptions.Item label="状态">{currentClue.resolved ? "已处理" : "待处理"}</Descriptions.Item>
            </Descriptions>
            <Form form={resolveForm} layout="vertical" onFinish={resolveClue}>
              <Form.Item name="resolution_note" label="处理记录">
                <Input.TextArea rows={4} placeholder="填写处理记录" />
              </Form.Item>
              <Button type="primary" htmlType="submit" icon={<CheckOutlined />} disabled={currentClue.resolved}>
                标记为已处理
              </Button>
            </Form>
          </Space>
        )}
      </Drawer>
    </div>
  );
}
