"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dayjs, { type Dayjs } from "dayjs";
import {
  App,
  Alert,
  Button,
  Card,
  DatePicker,
  Descriptions,
  Empty,
  Form,
  Image,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import {
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api, { extractErrorMessage } from "@/lib/api";
import { STATUS_COLORS, STATUS_TOKEN_COLORS } from "@/lib/status-colors";

const { Title, Text } = Typography;
const { RangePicker } = DatePicker;

const RULE_TYPES = [
  {
    value: "scan",
    label: "扫码积分",
    description: "消费者每次有效扫码后获得积分。",
  },
  {
    value: "first_scan",
    label: "首扫奖励",
    description: "消费者首次扫码时获得一次额外奖励。",
  },
  {
    value: "register",
    label: "注册奖励",
    description: "消费者完成手机号留资或注册后获得积分。",
  },
  {
    value: "checkin",
    label: "签到积分",
    description: "消费者连续互动或签到时获得积分。",
  },
  {
    value: "repurchase",
    label: "复购导入",
    description: "导入复购行为后给消费者补发积分。",
  },
  {
    value: "activity",
    label: "活动行为",
    description: "消费者参与指定活动后获得积分。",
  },
];

interface Overview {
  enabled_rules: number;
  active_products: number;
  points_awarded_7d: number;
  points_spent_7d: number;
  redemptions_7d: number;
  low_stock_products: number;
}

interface ConsumerProfile {
  id: string;
  nickname?: string | null;
  phone?: string | null;
  member_level: string;
  total_points: number;
  recent_transactions?: PointTransaction[];
}

interface PointTransaction {
  id: string;
  amount: number;
  balance_after: number;
  txn_type: "earning" | "spending" | "expired";
  reason?: string;
  created_at?: string | null;
  expires_at?: string | null;
}

interface PointRule {
  id: string;
  rule_type: string;
  points: number;
  daily_limit: number;
  description?: string;
  enabled: boolean;
  config?: Record<string, unknown>;
}

interface BenefitOption {
  id: string;
  name: string;
  stock_total?: number;
  stock_used?: number;
  status?: string;
}

interface PointProduct {
  id: string;
  name: string;
  description?: string | null;
  image_url?: string | null;
  points_cost: number;
  stock: number;
  total_claimed: number;
  enabled: boolean;
  benefit_id?: string | null;
  starts_at?: string | null;
  ends_at?: string | null;
  per_consumer_limit: number;
  sort_order: number;
}

interface PointRedemption {
  id: string;
  consumer_id: string;
  consumer_phone?: string | null;
  consumer_nickname?: string | null;
  product_id: string;
  product_name: string;
  points_cost: number;
  benefit_id?: string | null;
  benefit_name?: string | null;
  status: string;
  created_at?: string | null;
}

function formatDate(value?: string | null) {
  return value ? dayjs(value).format("YYYY-MM-DD HH:mm") : "不限制";
}

function ruleLabel(type: string) {
  return RULE_TYPES.find((item) => item.value === type)?.label || type;
}

function MemberOverview({
  overview,
  loading,
}: {
  overview: Overview | null;
  loading: boolean;
}) {
  const stats = [
    { title: "启用规则", value: overview?.enabled_rules ?? 0 },
    { title: "上架商品", value: overview?.active_products ?? 0 },
    { title: "7 日发放", value: overview?.points_awarded_7d ?? 0 },
    { title: "7 日消耗", value: overview?.points_spent_7d ?? 0 },
    { title: "7 日兑换", value: overview?.redemptions_7d ?? 0 },
    {
      title: "低库存",
      value: overview?.low_stock_products ?? 0,
      color:
        (overview?.low_stock_products ?? 0) > 0
          ? STATUS_TOKEN_COLORS.error
          : undefined,
    },
  ];
  return (
    <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-6">
      {stats.map((stat) => (
        <Card key={stat.title} size="small" loading={loading}>
          <Statistic
            title={stat.title}
            value={stat.value}
            styles={{ content: { color: stat.color } }}
          />
        </Card>
      ))}
    </div>
  );
}

function ConsumersTab({ onChanged }: { onChanged: () => void }) {
  const [keyword, setKeyword] = useState("");
  const [lookupType, setLookupType] = useState("auto");
  const [results, setResults] = useState<ConsumerProfile[]>([]);
  const [profile, setProfile] = useState<ConsumerProfile | null>(null);
  const [txns, setTxns] = useState<PointTransaction[]>([]);
  const [txnTotal, setTxnTotal] = useState(0);
  const [txnPage, setTxnPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [adjustType, setAdjustType] = useState<"award" | "spend" | null>(null);
  const [adjustForm] = Form.useForm();
  const { message } = App.useApp();
  const adjustPoints = Form.useWatch("points", adjustForm) as
    number | undefined;

  const fetchTxns = useCallback(async (consumerId: string, page = 1) => {
    const { data } = await api.get(
      `/members/consumers/${consumerId}/transactions`,
      { params: { page, page_size: 20 } }
    );
    setTxns(data.items || []);
    setTxnTotal(data.total || 0);
    setTxnPage(page);
  }, []);

  const fetchProfile = useCallback(
    async (consumerId: string) => {
      setLoading(true);
      try {
        const { data } = await api.get(`/members/consumers/${consumerId}`);
        setProfile(data);
        await fetchTxns(consumerId, 1);
      } catch {
        message.error("未找到消费者");
        setProfile(null);
        setTxns([]);
      } finally {
        setLoading(false);
      }
    },
    [fetchTxns, message]
  );

  const handleSearch = async () => {
    if (!keyword.trim()) return;
    setLoading(true);
    try {
      const { data } = await api.get("/members/consumers/search", {
        params: { keyword, lookup_type: lookupType },
      });
      const items = data.items || [];
      setResults(items);
      if (items.length === 1) await fetchProfile(items[0].id);
      if (items.length === 0) {
        setProfile(null);
        setTxns([]);
        message.info("没有匹配的消费者");
      }
    } catch {
      message.error("查询失败");
    } finally {
      setLoading(false);
    }
  };

  const openAdjust = (type: "award" | "spend") => {
    setAdjustType(type);
    adjustForm.resetFields();
  };

  const submitAdjust = async (values: { points: number; reason: string }) => {
    if (!profile || !adjustType) return;
    try {
      await api.post(`/members/points/${adjustType}`, {
        ...values,
        consumer_id: profile.id,
      });
      message.success(adjustType === "award" ? "积分已发放" : "积分已扣减");
      setAdjustType(null);
      await fetchProfile(profile.id);
      onChanged();
    } catch (e: unknown) {
      message.error(extractErrorMessage(e, "操作失败"));
    }
  };

  const resultColumns: ColumnsType<ConsumerProfile> = [
    {
      title: "消费者",
      render: (_, record) => record.nickname || record.phone || record.id,
    },
    {
      title: "手机号",
      dataIndex: "phone",
      render: (value) => value || "未留资",
    },
    { title: "会员等级", dataIndex: "member_level" },
    { title: "积分余额", dataIndex: "total_points" },
    {
      title: "操作",
      render: (_, record) => (
        <Button size="small" onClick={() => fetchProfile(record.id)}>
          查看
        </Button>
      ),
    },
  ];

  const txnColumns: ColumnsType<PointTransaction> = [
    {
      title: "类型",
      dataIndex: "txn_type",
      render: (type) => (
        <Tag
          color={
            type === "earning"
              ? STATUS_COLORS.success
              : type === "expired"
                ? STATUS_COLORS.neutral
                : STATUS_COLORS.warning
          }
        >
          {type === "earning" ? "收入" : type === "expired" ? "过期" : "支出"}
        </Tag>
      ),
    },
    { title: "数量", dataIndex: "amount" },
    { title: "余额", dataIndex: "balance_after" },
    {
      title: "原因",
      dataIndex: "reason",
      render: (value) => value || "未填写",
    },
    { title: "时间", dataIndex: "created_at", render: formatDate },
  ];

  const afterBalance = profile
    ? profile.total_points +
      (adjustType === "spend" ? -(adjustPoints || 0) : adjustPoints || 0)
    : 0;

  return (
    <div>
      <Alert
        className="mb-4"
        showIcon
        type="info"
        title="查询消费者后，可以查看积分余额、会员等级、积分明细，并执行发放或扣减。"
      />
      <Space.Compact className="mb-4 w-full max-w-190">
        <Select
          value={lookupType}
          onChange={setLookupType}
          options={[
            { value: "auto", label: "自动识别" },
            { value: "phone", label: "手机号" },
            { value: "id", label: "消费者 ID" },
            { value: "nickname", label: "昵称" },
          ]}
          style={{ width: 128 }}
        />
        <Input
          allowClear
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          onPressEnter={handleSearch}
          placeholder="输入手机号、消费者 ID 或昵称"
        />
        <Button
          type="primary"
          icon={<SearchOutlined />}
          loading={loading}
          onClick={handleSearch}
        >
          查询
        </Button>
      </Space.Compact>

      {!profile && results.length === 0 && (
        <Empty
          description="先查询消费者，再进行积分查看和调整。"
          image={Empty.PRESENTED_IMAGE_SIMPLE}
        />
      )}

      {!profile && results.length > 1 && (
        <Table
          columns={resultColumns}
          dataSource={results}
          rowKey="id"
          pagination={false}
          size="small"
        />
      )}

      {profile && (
        <>
          <Descriptions bordered size="small" column={3} className="mb-4">
            <Descriptions.Item label="消费者">
              {profile.nickname || profile.phone || profile.id}
            </Descriptions.Item>
            <Descriptions.Item label="手机号">
              {profile.phone || "未留资"}
            </Descriptions.Item>
            <Descriptions.Item label="会员等级">
              {profile.member_level}
            </Descriptions.Item>
            <Descriptions.Item label="积分余额">
              {profile.total_points}
            </Descriptions.Item>
            <Descriptions.Item label="消费者 ID" span={2}>
              {profile.id}
            </Descriptions.Item>
          </Descriptions>
          <Space className="mb-4">
            <Button type="primary" onClick={() => openAdjust("award")}>
              发放积分
            </Button>
            <Button onClick={() => openAdjust("spend")}>扣减积分</Button>
          </Space>
          <Table
            columns={txnColumns}
            dataSource={txns}
            rowKey="id"
            size="small"
            pagination={{
              current: txnPage,
              total: txnTotal,
              pageSize: 20,
              onChange: (page) => fetchTxns(profile.id, page),
              showTotal: (total) => `共 ${total} 条`,
            }}
          />
        </>
      )}

      <Modal
        title={adjustType === "award" ? "发放积分" : "扣减积分"}
        open={!!adjustType}
        onCancel={() => setAdjustType(null)}
        onOk={() => adjustForm.submit()}
        okButtonProps={{ disabled: adjustType === "spend" && afterBalance < 0 }}
        destroyOnHidden
      >
        {profile && (
          <Alert
            className="mb-4"
            type={afterBalance < 0 ? "error" : "info"}
            title={`当前余额 ${profile.total_points}，操作后余额 ${afterBalance}`}
            showIcon
          />
        )}
        <Form form={adjustForm} layout="vertical" onFinish={submitAdjust}>
          <Form.Item
            name="points"
            label="积分数"
            rules={[{ required: true, message: "请输入积分数" }]}
          >
            <InputNumber min={1} className="w-full" />
          </Form.Item>
          <Form.Item
            name="reason"
            label="原因"
            rules={[{ required: true, message: "请输入操作原因" }]}
          >
            <Input
              maxLength={200}
              placeholder={
                adjustType === "award"
                  ? "例如：客服补发扫码奖励"
                  : "例如：线下兑换扣减"
              }
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

function RulesTab({ onChanged }: { onChanged: () => void }) {
  const [items, setItems] = useState<PointRule[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [editItem, setEditItem] = useState<PointRule | null>(null);
  const [form] = Form.useForm();
  const { message } = App.useApp();

  const fetchRules = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/members/point-rules");
      setItems(Array.isArray(data) ? data : []);
    } catch {
      message.error("加载积分规则失败");
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    fetchRules();
  }, [fetchRules]);

  const openCreate = () => {
    setEditItem(null);
    form.resetFields();
    form.setFieldsValue({ daily_limit_unlimited: true });
    setOpen(true);
  };

  const openEdit = (record: PointRule) => {
    setEditItem(record);
    form.setFieldsValue({
      rule_type: record.rule_type,
      points: record.points,
      daily_limit_unlimited: !record.daily_limit,
      daily_limit: record.daily_limit || undefined,
      description: record.description,
    });
    setOpen(true);
  };

  const submitRule = async (values: Record<string, unknown>) => {
    const payload = {
      rule_type: values.rule_type,
      points: values.points,
      daily_limit: values.daily_limit_unlimited ? 0 : values.daily_limit || 0,
      description: values.description,
    };
    try {
      if (editItem)
        await api.put(`/members/point-rules/${editItem.id}`, payload);
      else await api.post("/members/point-rules", payload);
      message.success(editItem ? "规则已更新" : "规则已创建");
      setOpen(false);
      await fetchRules();
      onChanged();
    } catch (e: unknown) {
      message.error(extractErrorMessage(e, "保存失败"));
    }
  };

  const handleToggle = async (record: PointRule, enabled: boolean) => {
    try {
      await api.put(`/members/point-rules/${record.id}`, { enabled });
      message.success(
        enabled ? "规则已启用" : "规则已停用，后续行为不再触发此规则"
      );
      await fetchRules();
      onChanged();
    } catch {
      message.error("操作失败");
    }
  };

  const handleDelete = async (record: PointRule) => {
    try {
      await api.delete(`/members/point-rules/${record.id}`);
      message.success("规则已删除");
      await fetchRules();
      onChanged();
    } catch {
      message.error("删除失败");
    }
  };

  const columns: ColumnsType<PointRule> = [
    { title: "规则类型", dataIndex: "rule_type", render: ruleLabel },
    { title: "积分数", dataIndex: "points" },
    {
      title: "每日上限",
      dataIndex: "daily_limit",
      render: (value: number) => (value > 0 ? value : "不限制"),
    },
    {
      title: "说明",
      dataIndex: "description",
      render: (value) => value || "未填写",
    },
    {
      title: "状态",
      dataIndex: "enabled",
      render: (enabled: boolean, record) => (
        <Popconfirm
          title={
            enabled
              ? "停用后，后续消费者行为不会再触发该规则。"
              : "启用后，后续符合条件的消费者行为会触发积分发放。"
          }
          onConfirm={() => handleToggle(record, !enabled)}
        >
          <Switch checked={enabled} size="small" />
        </Popconfirm>
      ),
    },
    {
      title: "操作",
      width: 120,
      render: (_, record) => (
        <Space size="small">
          <Tooltip title="编辑规则">
            <Button
              size="small"
              type="text"
              icon={<EditOutlined />}
              onClick={() => openEdit(record)}
            />
          </Tooltip>
          <Popconfirm
            title="删除后不会影响历史积分，但后续行为无法再使用该规则。"
            onConfirm={() => handleDelete(record)}
          >
            <Tooltip title="删除规则">
              <Button
                size="small"
                type="text"
                danger
                icon={<DeleteOutlined />}
              />
            </Tooltip>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div className="mb-4 grid gap-3 md:grid-cols-3">
        {RULE_TYPES.slice(0, 3).map((rule) => (
          <Alert
            key={rule.value}
            type="info"
            showIcon
            title={rule.label}
            description={rule.description}
          />
        ))}
      </div>
      <div className="mb-4 flex justify-end">
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          新建规则
        </Button>
      </div>
      <Table
        columns={columns}
        dataSource={items}
        rowKey="id"
        loading={loading}
        pagination={false}
        locale={{
          emptyText: (
            <Empty
              description="还没有积分规则。创建后，消费者行为才能自动获得积分。"
              image={Empty.PRESENTED_IMAGE_SIMPLE}
            />
          ),
        }}
      />
      <Modal
        title={editItem ? "编辑积分规则" : "新建积分规则"}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={submitRule}>
          {!editItem && (
            <Form.Item
              name="rule_type"
              label="规则类型"
              rules={[{ required: true, message: "请选择规则类型" }]}
            >
              <Select
                options={RULE_TYPES.map(({ value, label }) => ({
                  value,
                  label,
                }))}
              />
            </Form.Item>
          )}
          <Form.Item
            name="points"
            label="积分数"
            rules={[{ required: true, message: "请输入积分数" }]}
          >
            <InputNumber min={1} className="w-full" />
          </Form.Item>
          <Form.Item
            name="daily_limit_unlimited"
            label="每日上限"
            valuePropName="checked"
          >
            <Switch checkedChildren="不限制" unCheckedChildren="限制" />
          </Form.Item>
          <Form.Item
            noStyle
            shouldUpdate={(prev, cur) =>
              prev.daily_limit_unlimited !== cur.daily_limit_unlimited
            }
          >
            {({ getFieldValue }) =>
              !getFieldValue("daily_limit_unlimited") && (
                <Form.Item
                  name="daily_limit"
                  label="每日最多发放次数/积分"
                  rules={[{ required: true, message: "请输入每日上限" }]}
                >
                  <InputNumber min={1} className="w-full" />
                </Form.Item>
              )
            }
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input
              maxLength={200}
              placeholder="说明该规则适用的消费者行为和限制"
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

function ProductsTab({ onChanged }: { onChanged: () => void }) {
  const [items, setItems] = useState<PointProduct[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [editItem, setEditItem] = useState<PointProduct | null>(null);
  const [benefits, setBenefits] = useState<BenefitOption[]>([]);
  const [form] = Form.useForm();
  const { message } = App.useApp();

  const benefitName = useMemo(
    () => Object.fromEntries(benefits.map((item) => [item.id, item.name])),
    [benefits]
  );

  const fetchProducts = useCallback(
    async (nextPage = 1) => {
      setLoading(true);
      try {
        const { data } = await api.get("/members/point-products", {
          params: { page: nextPage, page_size: 20 },
        });
        setItems(data.items || []);
        setTotal(data.total || 0);
        setPage(nextPage);
      } catch {
        message.error("加载积分商品失败");
      } finally {
        setLoading(false);
      }
    },
    [message]
  );

  const fetchBenefits = useCallback(async () => {
    try {
      const { data } = await api.get("/benefits", {
        params: { page_size: 100 },
      });
      setBenefits(data.items || []);
    } catch {
      setBenefits([]);
    }
  }, []);

  useEffect(() => {
    fetchProducts();
    fetchBenefits();
  }, [fetchBenefits, fetchProducts]);

  const openCreate = () => {
    setEditItem(null);
    form.resetFields();
    form.setFieldsValue({
      enabled: true,
      per_consumer_limit_unlimited: false,
      per_consumer_limit: 1,
      sort_order: 0,
    });
    setOpen(true);
  };

  const openEdit = (record: PointProduct) => {
    setEditItem(record);
    form.setFieldsValue({
      ...record,
      valid_range:
        record.starts_at || record.ends_at
          ? [
              record.starts_at ? dayjs(record.starts_at) : null,
              record.ends_at ? dayjs(record.ends_at) : null,
            ]
          : undefined,
      per_consumer_limit_unlimited: record.per_consumer_limit === 0,
      per_consumer_limit: record.per_consumer_limit || undefined,
    });
    setOpen(true);
  };

  const submitProduct = async (values: Record<string, unknown>) => {
    const range = values.valid_range as
      [Dayjs | null, Dayjs | null] | undefined;
    const payload = {
      name: values.name,
      description: values.description,
      image_url: values.image_url,
      points_cost: values.points_cost,
      stock: values.stock,
      benefit_id: values.benefit_id || null,
      starts_at: range?.[0]?.toISOString() || null,
      ends_at: range?.[1]?.toISOString() || null,
      per_consumer_limit: values.per_consumer_limit_unlimited
        ? 0
        : values.per_consumer_limit || 1,
      sort_order: values.sort_order || 0,
    };
    try {
      if (editItem)
        await api.put(`/members/point-products/${editItem.id}`, payload);
      else await api.post("/members/point-products", payload);
      message.success(editItem ? "商品已更新" : "商品已创建");
      setOpen(false);
      await fetchProducts(page);
      onChanged();
    } catch (e: unknown) {
      message.error(extractErrorMessage(e, "保存失败"));
    }
  };

  const handleDelete = async (record: PointProduct) => {
    try {
      await api.delete(`/members/point-products/${record.id}`);
      message.success("商品已删除");
      await fetchProducts(page);
      onChanged();
    } catch {
      message.error("删除失败");
    }
  };

  const columns: ColumnsType<PointProduct> = [
    {
      title: "商品",
      render: (_, record) => (
        <Space>
          {record.image_url ? (
            <Image
              src={record.image_url}
              alt={record.name}
              width={48}
              height={48}
              className="rounded object-cover"
            />
          ) : (
            <div className="h-12 w-12 rounded bg-bg-muted" />
          )}
          <div>
            <div className="font-medium">{record.name}</div>
            <Text type="secondary" className="text-xs">
              {record.description || "未填写描述"}
            </Text>
          </div>
        </Space>
      ),
    },
    {
      title: "积分价格",
      dataIndex: "points_cost",
      render: (value) => <Tag color={STATUS_COLORS.warning}>{value} 积分</Tag>,
    },
    {
      title: "库存",
      render: (_, record) => `${record.stock} / 已兑 ${record.total_claimed}`,
    },
    {
      title: "每人限兑",
      dataIndex: "per_consumer_limit",
      render: (value: number) => (value > 0 ? value : "不限制"),
    },
    {
      title: "有效期",
      render: (_, record) =>
        `${formatDate(record.starts_at)} 至 ${formatDate(record.ends_at)}`,
    },
    {
      title: "关联权益",
      dataIndex: "benefit_id",
      render: (value) => (value ? benefitName[value] || value : "未关联"),
    },
    {
      title: "状态",
      dataIndex: "enabled",
      render: (enabled: boolean, record: PointProduct) => (
        <Switch
          checked={enabled}
          checkedChildren="上架"
          unCheckedChildren="下架"
          onChange={async (checked) => {
            try {
              await api.put(`/members/point-products/${record.id}`, {
                enabled: checked,
              });
              message.success(checked ? "已上架" : "已下架");
              await fetchProducts(page);
            } catch {
              message.error("操作失败");
            }
          }}
        />
      ),
    },
    {
      title: "操作",
      width: 120,
      render: (_, record) => (
        <Space size="small">
          <Tooltip title="编辑商品">
            <Button
              size="small"
              type="text"
              icon={<EditOutlined />}
              onClick={() => openEdit(record)}
            />
          </Tooltip>
          <Popconfirm
            title="删除后商品不再展示，历史兑换记录会保留。"
            onConfirm={() => handleDelete(record)}
          >
            <Tooltip title="删除商品">
              <Button
                size="small"
                type="text"
                danger
                icon={<DeleteOutlined />}
              />
            </Tooltip>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Alert
        className="mb-4"
        type="info"
        showIcon
        title="商品上架后，消费者可在 H5 积分商城中使用积分兑换。"
      />
      <div className="mb-4 flex justify-end">
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          新增商品
        </Button>
      </div>
      <Table
        columns={columns}
        dataSource={items}
        rowKey="id"
        loading={loading}
        pagination={{
          current: page,
          total,
          pageSize: 20,
          onChange: fetchProducts,
          showTotal: (value) => `共 ${value} 个商品`,
        }}
        locale={{
          emptyText: (
            <Empty
              description="还没有积分商品。添加后，消费者可在积分商城兑换。"
              image={Empty.PRESENTED_IMAGE_SIMPLE}
            />
          ),
        }}
      />
      <Modal
        title={editItem ? "编辑积分商品" : "新增积分商品"}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        width={680}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={submitProduct}>
          <Form.Item
            name="name"
            label="商品名称"
            rules={[{ required: true, message: "请输入商品名称" }]}
          >
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={3} maxLength={500} />
          </Form.Item>
          <Form.Item name="image_url" label="图片 URL">
            <Input maxLength={500} placeholder="https://..." />
          </Form.Item>
          <div className="grid grid-cols-2 gap-4">
            <Form.Item
              name="points_cost"
              label="积分价格"
              rules={[{ required: true, message: "请输入积分价格" }]}
            >
              <InputNumber min={1} className="w-full" />
            </Form.Item>
            <Form.Item
              name="stock"
              label="库存"
              rules={[{ required: true, message: "请输入库存" }]}
            >
              <InputNumber min={0} className="w-full" />
            </Form.Item>
          </div>
          <Form.Item name="valid_range" label="兑换有效期">
            <RangePicker className="w-full" showTime />
          </Form.Item>
          <div className="grid grid-cols-2 gap-4">
            <Form.Item
              name="per_consumer_limit_unlimited"
              label="每人限兑"
              valuePropName="checked"
            >
              <Switch checkedChildren="不限制" unCheckedChildren="限制" />
            </Form.Item>
            <Form.Item
              noStyle
              shouldUpdate={(prev, cur) =>
                prev.per_consumer_limit_unlimited !==
                cur.per_consumer_limit_unlimited
              }
            >
              {({ getFieldValue }) =>
                !getFieldValue("per_consumer_limit_unlimited") && (
                  <Form.Item
                    name="per_consumer_limit"
                    label="每人最多兑换次数"
                    rules={[{ required: true, message: "请输入限兑次数" }]}
                  >
                    <InputNumber min={1} className="w-full" />
                  </Form.Item>
                )
              }
            </Form.Item>
          </div>
          <Form.Item name="benefit_id" label="关联权益">
            <Select
              allowClear
              showSearch
              optionFilterProp="label"
              options={benefits.map((item) => ({
                value: item.id,
                label: item.name,
              }))}
            />
          </Form.Item>
          <Form.Item name="sort_order" label="排序">
            <InputNumber min={0} className="w-full" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

function RedemptionsTab() {
  const [items, setItems] = useState<PointRedemption[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [filters, setFilters] = useState<{
    consumer_id?: string;
    product_id?: string;
    status?: string;
  }>({});
  const { message } = App.useApp();
  const initialized = useRef(false);

  const fetchRedemptions = useCallback(
    async (nextPage = 1, nextFilters = filters) => {
      setLoading(true);
      try {
        const { data } = await api.get("/members/point-redemptions", {
          params: { page: nextPage, page_size: 20, ...nextFilters },
        });
        setItems(data.items || []);
        setTotal(data.total || 0);
        setPage(nextPage);
      } catch {
        message.error("加载兑换记录失败");
      } finally {
        setLoading(false);
      }
    },
    [filters, message]
  );

  useEffect(() => {
    if (!initialized.current) {
      initialized.current = true;
      fetchRedemptions(1, {});
    }
  }, []);

  const columns: ColumnsType<PointRedemption> = [
    {
      title: "消费者",
      render: (_, record) =>
        record.consumer_nickname ||
        record.consumer_phone ||
        `${record.consumer_id.slice(0, 8)}...`,
    },
    { title: "商品", dataIndex: "product_name" },
    {
      title: "消耗积分",
      dataIndex: "points_cost",
      render: (value) => <Tag color={STATUS_COLORS.warning}>{value} 积分</Tag>,
    },
    {
      title: "关联权益",
      dataIndex: "benefit_name",
      render: (value) => value || "未关联",
    },
    {
      title: "状态",
      dataIndex: "status",
      render: (value) => (
        <Tag
          color={
            value === "success" ? STATUS_COLORS.success : STATUS_COLORS.error
          }
        >
          {value === "success" ? "成功" : "失败"}
        </Tag>
      ),
    },
    { title: "兑换时间", dataIndex: "created_at", render: formatDate },
  ];

  const applyFilters = () => {
    const normalized = Object.fromEntries(
      Object.entries(filters).filter(([, value]) => value)
    );
    setFilters(normalized);
    fetchRedemptions(1, normalized);
  };

  return (
    <div>
      <Space className="mb-4" wrap>
        <Input
          allowClear
          placeholder="消费者 ID"
          value={filters.consumer_id}
          onChange={(event) =>
            setFilters((prev) => ({ ...prev, consumer_id: event.target.value }))
          }
          style={{ width: 260 }}
        />
        <Input
          allowClear
          placeholder="商品 ID"
          value={filters.product_id}
          onChange={(event) =>
            setFilters((prev) => ({ ...prev, product_id: event.target.value }))
          }
          style={{ width: 260 }}
        />
        <Select
          allowClear
          placeholder="状态"
          value={filters.status}
          onChange={(value) =>
            setFilters((prev) => ({ ...prev, status: value }))
          }
          options={[
            { value: "success", label: "成功" },
            { value: "failed", label: "失败" },
          ]}
          style={{ width: 120 }}
        />
        <Button onClick={applyFilters}>筛选</Button>
      </Space>
      <Table
        columns={columns}
        dataSource={items}
        rowKey="id"
        loading={loading}
        pagination={{
          current: page,
          total,
          pageSize: 20,
          onChange: (nextPage) => fetchRedemptions(nextPage),
          showTotal: (value) => `共 ${value} 条`,
        }}
        locale={{
          emptyText: (
            <Empty
              description="暂无兑换记录。消费者完成兑换后会显示在这里。"
              image={Empty.PRESENTED_IMAGE_SIMPLE}
            />
          ),
        }}
      />
    </div>
  );
}

export default function MembersPage() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [overviewLoading, setOverviewLoading] = useState(false);
  const { message } = App.useApp();

  const fetchOverview = useCallback(async () => {
    setOverviewLoading(true);
    try {
      const { data } = await api.get("/members/overview");
      setOverview(data);
    } catch {
      message.error("加载会员积分概览失败");
    } finally {
      setOverviewLoading(false);
    }
  }, [message]);

  useEffect(() => {
    fetchOverview();
  }, [fetchOverview]);

  return (
    <div>
      <div className="mb-4">
        <Title level={4} className="!mb-1">
          会员积分
        </Title>
        <Text type="secondary">
          配置积分获取规则和兑换商品，跟踪消费者积分变动与兑换结果。
        </Text>
      </div>
      <MemberOverview overview={overview} loading={overviewLoading} />
      <Tabs
        defaultActiveKey="consumers"
        items={[
          {
            key: "consumers",
            label: "消费者查询",
            children: <ConsumersTab onChanged={fetchOverview} />,
          },
          {
            key: "rules",
            label: "积分规则",
            children: <RulesTab onChanged={fetchOverview} />,
          },
          {
            key: "products",
            label: "积分商城",
            children: <ProductsTab onChanged={fetchOverview} />,
          },
          {
            key: "redemptions",
            label: "兑换记录",
            children: <RedemptionsTab />,
          },
        ]}
      />
    </div>
  );
}
