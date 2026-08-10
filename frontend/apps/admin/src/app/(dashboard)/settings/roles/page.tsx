"use client";

import useSWR from "swr";
import {
  Alert,
  Button,
  Card,
  Empty,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useAuthStore } from "@/lib/auth";
import { canViewRoleDirectory } from "@/lib/account-access";
import { extractErrorMessage } from "@/lib/api";

const { Title, Text } = Typography;

interface Permission {
  id: string;
  code: string;
  description?: string;
}

interface Role {
  id: string;
  name: string;
  description?: string;
  permissions: Permission[];
}

const ROLE_LABELS: Record<string, string> = {
  admin: "租户管理员",
  operator: "运营人员",
  viewer: "受限成员（无业务权限）",
};

export default function RolesPage() {
  const role = useAuthStore((state) => state.user?.role?.toLowerCase());
  const canView = canViewRoleDirectory(role);
  const {
    data: roles = [],
    error,
    isLoading,
    mutate,
  } = useSWR<Role[]>(canView ? "/roles" : null);

  const columns: ColumnsType<Role> = [
    {
      title: "角色",
      dataIndex: "name",
      width: 180,
      render: (name: string) => ROLE_LABELS[name] || name,
    },
    {
      title: "用途",
      dataIndex: "description",
      width: 260,
      render: (description?: string) => description || "—",
    },
    {
      title: "包含权限",
      dataIndex: "permissions",
      render: (permissions: Permission[]) => (
        <Space wrap size={[4, 8]}>
          {permissions.map((permission) => (
            <Tag key={permission.id}>
              {permission.description || permission.code}
            </Tag>
          ))}
          {permissions.length === 0 && (
            <Text type="secondary">无业务操作权限</Text>
          )}
        </Space>
      ),
    },
  ];

  if (!canView) {
    return (
      <Space orientation="vertical" size={16} style={{ width: "100%" }}>
        <Title level={4} style={{ marginBottom: 4 }}>
          角色与权限
        </Title>
        <Alert
          type="info"
          showIcon
          message="当前角色不能查看角色目录"
          description="如需了解或调整权限范围，请联系租户管理员处理。"
        />
      </Space>
    );
  }

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <div>
        <Title level={4} style={{ marginBottom: 4 }}>
          角色与权限
        </Title>
        <Text type="secondary">
          查看当前可分配给账号的内置角色及其权限范围。
        </Text>
      </div>
      <Alert
        type="info"
        showIcon
        message="当前使用经过验证的内置角色"
        description="如需调整成员权限，请到账号管理中重新分配角色。自定义角色将在所有业务模块完成细粒度权限接入后开放。"
      />
      {error && (
        <Alert
          type="error"
          showIcon
          message="角色目录加载失败"
          description={extractErrorMessage(error, "请检查网络后重试")}
          action={
            <Button size="small" onClick={() => void mutate()}>
              重新加载
            </Button>
          }
        />
      )}
      {!error && (
        <Card>
          {!isLoading && roles.length === 0 ? (
            <Empty description="当前没有可分配的内置角色" />
          ) : (
            <Table<Role>
              rowKey="id"
              columns={columns}
              dataSource={roles}
              loading={isLoading}
              pagination={false}
              size="middle"
            />
          )}
        </Card>
      )}
    </Space>
  );
}
