"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Table,
  Button,
  Space,
  Modal,
  Form,
  Input,
  Checkbox,
  Switch,
  Popconfirm,
  Typography,
  Tag,
  message,
} from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";

const { Title } = Typography;

interface Role {
  id: string;
  name: string;
  description: string;
  permissions: string[];
  is_active: boolean;
}

const PERMISSION_GROUPS = [
  { label: "品牌管理", permissions: ["brand:read", "brand:write"] },
  { label: "产品管理", permissions: ["product:read", "product:write"] },
  { label: "码管理", permissions: ["code:read", "code:write", "code:export"] },
  { label: "页面管理", permissions: ["page:read", "page:write", "page:publish"] },
  { label: "活动管理", permissions: ["campaign:read", "campaign:write"] },
  { label: "权益管理", permissions: ["benefit:read", "benefit:write"] },
  { label: "统计查看", permissions: ["analytics:read", "analytics:export"] },
  { label: "系统设置", permissions: ["settings:read", "settings:write"] },
];

const PERMISSION_LABEL_MAP: Record<string, string> = {
  "brand:read": "查看品牌",
  "brand:write": "编辑品牌",
  "product:read": "查看产品",
  "product:write": "编辑产品",
  "code:read": "查看码",
  "code:write": "编辑码",
  "code:export": "导出码",
  "page:read": "查看页面",
  "page:write": "编辑页面",
  "page:publish": "发布页面",
  "campaign:read": "查看活动",
  "campaign:write": "编辑活动",
  "benefit:read": "查看权益",
  "benefit:write": "编辑权益",
  "analytics:read": "查看统计",
  "analytics:export": "导出统计",
  "settings:read": "查看设置",
  "settings:write": "编辑设置",
};

export default function RolesPage() {
  const [roles, setRoles] = useState<Role[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingRole, setEditingRole] = useState<Role | null>(null);
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);

  const fetchRoles = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/roles", {
        params: { page, page_size: 20 },
      });
      setRoles(data.items || []);
      setTotal(data.total || 0);
    } catch {
      message.error("加载角色列表失败");
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => {
    fetchRoles();
  }, [fetchRoles]);

  const openCreateModal = () => {
    setEditingRole(null);
    form.resetFields();
    setModalOpen(true);
  };

  const openEditModal = (role: Role) => {
    setEditingRole(role);
    form.setFieldsValue({
      name: role.name,
      description: role.description,
      permissions: role.permissions,
    });
    setModalOpen(true);
  };

  const handleSave = async (values: {
    name: string;
    description: string;
    permissions: string[];
  }) => {
    setSaving(true);
    try {
      if (editingRole) {
        await api.patch(`/roles/${editingRole.id}`, values);
        message.success("角色更新成功");
      } else {
        await api.post("/roles", values);
        message.success("角色创建成功");
      }
      setModalOpen(false);
      form.resetFields();
      setEditingRole(null);
      fetchRoles();
    } catch {
      message.error(editingRole ? "更新失败" : "创建失败");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (roleId: string) => {
    try {
      await api.delete(`/roles/${roleId}`);
      message.success("角色已删除");
      fetchRoles();
    } catch {
      message.error("删除失败");
    }
  };

  const handleToggleActive = async (role: Role) => {
    try {
      await api.patch(`/roles/${role.id}`, {
        is_active: !role.is_active,
      });
      message.success(role.is_active ? "角色已禁用" : "角色已启用");
      fetchRoles();
    } catch {
      message.error("操作失败");
    }
  };

  const columns: ColumnsType<Role> = [
    {
      title: "角色名称",
      dataIndex: "name",
      key: "name",
    },
    {
      title: "描述",
      dataIndex: "description",
      key: "description",
      ellipsis: true,
    },
    {
      title: "权限数",
      key: "permission_count",
      width: 100,
      render: (_, record) => (
        <Tag color="blue">{record.permissions?.length || 0} 项</Tag>
      ),
    },
    {
      title: "状态",
      dataIndex: "is_active",
      key: "is_active",
      width: 100,
      render: (active: boolean, record) => (
        <Switch
          checked={active}
          onChange={() => handleToggleActive(record)}
          checkedChildren="启用"
          unCheckedChildren="禁用"
        />
      ),
    },
    {
      title: "操作",
      key: "action",
      width: 180,
      render: (_, record) => (
        <Space>
          <Button size="small" onClick={() => openEditModal(record)}>
            编辑
          </Button>
          <Popconfirm
            title="确认删除此角色？"
            description="删除后不可恢复"
            onConfirm={() => handleDelete(record.id)}
            okText="删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
          >
            <Button size="small" danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Title level={4} className="!mb-0">
          角色权限管理
        </Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreateModal}>
          新建角色
        </Button>
      </div>

      <Table
        columns={columns}
        dataSource={roles}
        rowKey="id"
        loading={loading}
        pagination={{
          current: page,
          total,
          pageSize: 20,
          onChange: setPage,
          showTotal: (t) => `共 ${t} 条`,
        }}
      />

      <Modal
        title={editingRole ? "编辑角色" : "新建角色"}
        open={modalOpen}
        onCancel={() => {
          setModalOpen(false);
          setEditingRole(null);
          form.resetFields();
        }}
        onOk={() => form.submit()}
        confirmLoading={saving}
        width={600}
      >
        <Form form={form} layout="vertical" onFinish={handleSave}>
          <Form.Item
            name="name"
            label="角色名称"
            rules={[{ required: true, message: "请输入角色名称" }]}
          >
            <Input placeholder="例如：运营专员" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} placeholder="角色描述" />
          </Form.Item>
          <Form.Item
            name="permissions"
            label="权限配置"
            rules={[{ required: true, message: "请至少选择一项权限" }]}
          >
            <Checkbox.Group style={{ width: "100%" }}>
              {PERMISSION_GROUPS.map((group) => (
                <div key={group.label} className="mb-4">
                  <div className="mb-2 font-medium">{group.label}</div>
                  <Space wrap>
                    {group.permissions.map((perm) => (
                      <Checkbox key={perm} value={perm}>
                        {PERMISSION_LABEL_MAP[perm] || perm}
                      </Checkbox>
                    ))}
                  </Space>
                </div>
              ))}
            </Checkbox.Group>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
