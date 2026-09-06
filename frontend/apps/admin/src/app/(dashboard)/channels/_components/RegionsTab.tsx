import type { ChannelsWorkspaceApi } from "./useChannelsWorkspace";
import { Button, Table } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ChannelAccess } from "@/lib/channel-access";

interface TabProps {
  w: ChannelsWorkspaceApi;
  access: ChannelAccess;
}

export function RegionsTab({ w, access }: TabProps) {
  const { regions, loading, openEntityModal, regionColumns } = w;
  return (
    <>
      {access.canManage && (
        <div className="mb-4 flex justify-end">
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => openEntityModal("region")}
          >
            新建区域
          </Button>
        </div>
      )}
      <Table
        columns={regionColumns}
        dataSource={regions.items}
        rowKey="id"
        loading={loading}
      />
    </>
  );
}
