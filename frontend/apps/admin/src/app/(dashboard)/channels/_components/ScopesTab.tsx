import type { ChannelsWorkspaceApi } from "./useChannelsWorkspace";
import { Button, Table } from "antd";
import { SafetyCertificateOutlined } from "@ant-design/icons";
import type { ChannelAccess } from "@/lib/channel-access";

interface TabProps {
  w: ChannelsWorkspaceApi;
  access: ChannelAccess;
}

export function ScopesTab({ w, access }: TabProps) {
  const { scopes, loading, setScopeOpen, scopeColumns } = w;
  return (
    <>
      {access.canScope && (
        <div className="mb-4 flex justify-end">
          <Button
            type="primary"
            icon={<SafetyCertificateOutlined />}
            onClick={() => setScopeOpen(true)}
          >
            绑定入口账号
          </Button>
        </div>
      )}
      <Table
        columns={scopeColumns}
        dataSource={scopes}
        rowKey="id"
        loading={loading}
      />
    </>
  );
}
