import type { ChannelsWorkspaceApi } from "./useChannelsWorkspace";
import { Button, Empty, Table } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ChannelAccess } from "@/lib/channel-access";

interface TabProps {
  w: ChannelsWorkspaceApi;
  access: ChannelAccess;
}

export function DistributorsTab({ w, access }: TabProps) {
  const { distributors, loading, openEntityModal, distributorColumns } = w;
  return (
    <>
      {access.canManage && (
        <div className="mb-4 flex justify-end">
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => openEntityModal("distributor")}
          >
            新建经销商
          </Button>
        </div>
      )}
      <Table
        columns={distributorColumns}
        dataSource={distributors.items}
        rowKey="id"
        loading={loading}
        locale={{
          emptyText: <Empty description="先新建经销商，再绑定区域和门店" />,
        }}
      />
    </>
  );
}
