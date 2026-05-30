"use client";

import { useEffect, useState, useCallback } from "react";
import { App, Button, Card, Form, Input, Modal, Select, Space, Table, Tabs, Typography } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";

const { Title } = Typography;

interface Organization {
  id: string;
  name: string;
  parent_id?: string;
}

interface Account {
  id: string;
  email: string;
  name: string;
  organization_id: string;
  tenant_id: string;
}

export default function AccountsPage() {
  const { message } = App.useApp();
  const [activeTab, setActiveTab] = useState("orgs");
  const [orgs, setOrgs] = useState<Organization[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [loading, setLoading] = useState(false);
  const [orgModalOpen, setOrgModalOpen] = useState(false);
  const [accountModalOpen, setAccountModalOpen] = useState(false);
  const [orgForm] = Form.useForm();
  const [accountForm] = Form.useForm();

  const fetchOrgs = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/organizations");
      setOrgs(Array.isArray(data) ? data : data.items || []);
    } catch {
      message.error("加载组织列表失败");
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchAccounts = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/accounts");
      setAccounts(Array.isArray(data) ? data : data.items || []);
    } catch {
      message.error("加载账户列表失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (activeTab === "orgs") fetchOrgs();
    else fetchAccounts();
  }, [activeTab, fetchOrgs, fetchAccounts]);

  const handleCreateOrg = async (values: { name: string }) => {
    try {
      await api.post("/organizations", values);
      message.success("组织创建成功");
      setOrgModalOpen(false);
      orgForm.resetFields();
      fetchOrgs();
    } catch {
      message.error("创建失败");
    }
  };

  const handleCreateAccount = async (values: Record<string, string>) => {
    try {
      await api.post("/accounts", values);
      message.success("账户创建成功");
      setAccountModalOpen(false);
      accountForm.resetFields();
      fetchAccounts();
    } catch {
      message.error("创建失败");
    }
  };

  const orgColumns: ColumnsType<Organization> = [
    { title: "组织名称", dataIndex: "name", key: "name" },
    { title: "ID", dataIndex: "id", key: "id", render: (v: string) => v.slice(0, 8) + "..." },
  ];

  const accountColumns: ColumnsType<Account> = [
    { title: "姓名", dataIndex: "name", key: "name" },
    { title: "邮箱", dataIndex: "email", key: "email" },
    { title: "组织 ID", dataIndex: "organization_id", key: "organization_id", render: (v: string) => v.slice(0, 8) + "..." },
  ];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Title level={4} className="!mb-0">组织与账户</Title>
      </div>
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: "orgs",
            label: "组织管理",
            children: (
              <>
                <div className="mb-4">
                  <Button type="primary" icon={<PlusOutlined />} onClick={() => setOrgModalOpen(true)}>
                    新建组织
                  </Button>
                </div>
                <Table columns={orgColumns} dataSource={orgs} rowKey="id" loading={loading && activeTab === "orgs"} />
              </>
            ),
          },
          {
            key: "accounts",
            label: "账户管理",
            children: (
              <>
                <div className="mb-4">
                  <Button type="primary" icon={<PlusOutlined />} onClick={() => setAccountModalOpen(true)}>
                    新建账户
                  </Button>
                </div>
                <Table columns={accountColumns} dataSource={accounts} rowKey="id" loading={loading && activeTab === "accounts"} />
              </>
            ),
          },
        ]}
      />
      <Modal title="新建组织" open={orgModalOpen} onCancel={() => setOrgModalOpen(false)} onOk={() => orgForm.submit()}>
        <Form form={orgForm} layout="vertical" onFinish={handleCreateOrg}>
          <Form.Item name="name" label="组织名称" rules={[{ required: true, message: "请输入组织名称" }]}>
            <Input />
          </Form.Item>
        </Form>
      </Modal>
      <Modal title="新建账户" open={accountModalOpen} onCancel={() => setAccountModalOpen(false)} onOk={() => accountForm.submit()} width={500}>
        <Form form={accountForm} layout="vertical" onFinish={handleCreateAccount}>
          <Form.Item name="email" label="邮箱" rules={[{ required: true, type: "email", message: "请输入有效邮箱" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="name" label="姓名" rules={[{ required: true, message: "请输入姓名" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, min: 8, message: "密码至少 8 位" }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item name="organization_id" label="所属组织" rules={[{ required: true, message: "请选择组织" }]}>
            <Select placeholder="选择组织" options={orgs.map((o) => ({ value: o.id, label: o.name }))} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
