"use client";

import { SearchOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import {
  Alert,
  App,
  Button,
  Descriptions,
  Empty,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
} from "antd";
import dayjs from "dayjs";
import { useState } from "react";

import api from "@/lib/api";
import { STATUS_COLORS } from "@/lib/status-colors";
import { RepurchaseWorkbench } from "./RepurchaseWorkbench";

interface ConsumerProfile {
  id: string;
  nickname?: string | null;
  phone?: string | null;
  membership?: {
    id: string;
    number: string;
    status: "active" | "merged";
    joined_at?: string | null;
  } | null;
}

function formatDate(value?: string | null) {
  return value ? dayjs(value).format("YYYY-MM-DD HH:mm") : "—";
}

export function BrandMembersTable() {
  const [keyword, setKeyword] = useState("");
  const [lookupType, setLookupType] = useState("auto");
  const [results, setResults] = useState<ConsumerProfile[]>([]);
  const [selected, setSelected] = useState<ConsumerProfile | null>(null);
  const [searched, setSearched] = useState(false);
  const [loading, setLoading] = useState(false);
  const [piiOpen, setPiiOpen] = useState(false);
  const [fullPhone, setFullPhone] = useState<string | null>(null);
  const [piiForm] = Form.useForm();
  const { message } = App.useApp();

  const search = async () => {
    if (!keyword.trim()) return;
    setLoading(true);
    try {
      const { data } = await api.get("/members/consumers/search", {
        params: { keyword, lookup_type: lookupType },
      });
      const items = (data.items || []) as ConsumerProfile[];
      setResults(items);
      setSelected(items.length === 1 ? items[0] : null);
      setSearched(true);
      if (items.length === 0) message.info("没有匹配的会员或消费者档案");
    } catch {
      message.error("查询失败");
    } finally {
      setLoading(false);
    }
  };

  const revealPhone = async (values: {
    reason: string;
    ticket_ref?: string;
  }) => {
    if (!selected) return;
    try {
      const { data } = await api.post(
        `/privacy/members/${selected.id}/pii-reveal`,
        values
      );
      setFullPhone(data.phone || "未绑定手机号");
      message.success("本次完整信息查看已记录审计");
    } catch {
      message.error("当前账号没有完整 PII 查看权限，或密钥服务不可用");
    }
  };

  const columns: ColumnsType<ConsumerProfile> = [
    {
      title: "会员编号",
      render: (_, record) => record.membership?.number || "尚未入会",
    },
    {
      title: "消费者",
      render: (_, record) => record.nickname || record.phone || "未设置称呼",
    },
    {
      title: "手机号",
      dataIndex: "phone",
      render: (value) => value || "未绑定",
    },
    {
      title: "会员状态",
      render: (_, record) =>
        record.membership ? (
          <Tag color={STATUS_COLORS.success}>有效会员</Tag>
        ) : (
          <Tag>消费者档案</Tag>
        ),
    },
    {
      title: "入会时间",
      render: (_, record) => formatDate(record.membership?.joined_at),
    },
    {
      title: "操作",
      render: (_, record) => (
        <Button size="small" onClick={() => setSelected(record)}>
          查看
        </Button>
      ),
    },
  ];

  return (
    <>
      <Alert
        className="mb-4"
        showIcon
        type="info"
        title="消费者档案只有在取得明确入会证据后才显示为品牌会员；手机号留资或一次扫码不会自动入会。"
      />
      <Space.Compact className="mb-4 w-full max-w-190">
        <Select
          value={lookupType}
          onChange={setLookupType}
          options={[
            { value: "auto", label: "自动识别" },
            { value: "membership", label: "会员编号" },
            { value: "phone", label: "手机号" },
            { value: "nickname", label: "昵称" },
          ]}
          style={{ width: 132 }}
        />
        <Input
          allowClear
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          onPressEnter={() => void search()}
          placeholder="输入会员编号、手机号或昵称"
        />
        <Button
          type="primary"
          icon={<SearchOutlined />}
          loading={loading}
          onClick={() => void search()}
        >
          查询
        </Button>
      </Space.Compact>

      {!searched ? (
        <Empty
          description="输入业务可读的会员编号、手机号或昵称开始查询。"
          image={Empty.PRESENTED_IMAGE_SIMPLE}
        />
      ) : (
        <Table
          columns={columns}
          dataSource={results}
          rowKey="id"
          loading={loading}
          pagination={false}
          size="small"
          locale={{ emptyText: "没有匹配的会员或消费者档案" }}
        />
      )}

      {selected && (
        <Descriptions bordered size="small" column={2} className="mt-4">
          <Descriptions.Item label="会员编号">
            {selected.membership?.number || "尚未入会"}
          </Descriptions.Item>
          <Descriptions.Item label="会员状态">
            {selected.membership ? "有效会员" : "仅消费者档案"}
          </Descriptions.Item>
          <Descriptions.Item label="消费者称呼">
            {selected.nickname || "未设置"}
          </Descriptions.Item>
          <Descriptions.Item label="手机号">
            {selected.phone || "未绑定"}
          </Descriptions.Item>
          <Descriptions.Item label="入会时间" span={2}>
            {formatDate(selected.membership?.joined_at)}
          </Descriptions.Item>
          <Descriptions.Item label="高风险操作" span={2}>
            <Button
              size="small"
              onClick={() => {
                setFullPhone(null);
                setPiiOpen(true);
              }}
            >
              查看完整手机号
            </Button>
          </Descriptions.Item>
        </Descriptions>
      )}
      <Modal
        title="查看完整手机号"
        open={piiOpen}
        onCancel={() => {
          setPiiOpen(false);
          setFullPhone(null);
          piiForm.resetFields();
        }}
        onOk={
          fullPhone
            ? () => {
                setPiiOpen(false);
                setFullPhone(null);
                piiForm.resetFields();
              }
            : () => piiForm.submit()
        }
        okText={fullPhone ? "关闭" : "确认并记录审计"}
      >
        {fullPhone ? (
          <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label="完整手机号">
              {fullPhone}
            </Descriptions.Item>
          </Descriptions>
        ) : (
          <>
            <Alert
              className="mb-4"
              type="warning"
              showIcon
              title="仅在有明确业务目的时查看。每次尝试和结果都会进入不可变审计。"
            />
            <Form form={piiForm} layout="vertical" onFinish={revealPhone}>
              <Form.Item
                name="reason"
                label="查看原因"
                rules={[{ required: true, min: 2 }]}
              >
                <Input.TextArea rows={3} />
              </Form.Item>
              <Form.Item name="ticket_ref" label="关联工单（可选）">
                <Input maxLength={160} />
              </Form.Item>
            </Form>
          </>
        )}
      </Modal>
    </>
  );
}

export default function MembersPage() {
  return <RepurchaseWorkbench members={<BrandMembersTable />} />;
}
