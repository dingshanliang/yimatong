import { Typography } from "antd";
import type { ChannelsWorkspaceApi } from "./useChannelsWorkspace";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Tag,
  Timeline,
} from "antd";
import { CheckOutlined } from "@ant-design/icons";
import { STATUS_COLORS } from "@/lib/status-colors";
import {
  formatDateTime,
  productSkuLabel,
  resolutionActionLabel,
  severityTag,
} from "./shared";
import { PROVINCE_CITY_OPTIONS } from "./shared";
import type { ChannelAccess } from "@/lib/channel-access";

const { Text } = Typography;

interface ModalsProps {
  w: ChannelsWorkspaceApi;
  access: ChannelAccess;
}

export function ChannelsModals({ w, access }: ModalsProps) {
  const {
    regions,
    stores,
    accounts,
    batches,
    entityModal,
    entitySaving,
    createdDistributor,
    createdRegion,
    allocationOpen,
    setAllocationOpen,
    allocationToReassign,
    setAllocationToReassign,
    allocationToArchive,
    setAllocationToArchive,
    scopeOpen,
    setScopeOpen,
    currentClue,
    setCurrentClue,
    investigation,
    setInvestigation,
    entityForm,
    allocationForm,
    archiveAllocationForm,
    scopeForm,
    resolveForm,
    evidenceForm,
    regionCoverageType,
    regionProvince,
    allocationTargetType,
    selectedAllocationBatch,
    selectedBatchCapacity,
    allocationAvailableQuantity,
    distributorOptions,
    regionOptions,
    storeOptions,
    allocationDistributorName,
    allocationSummary,
    closeEntityModal,
    saveEntity,
    continueWithRegion,
    continueWithScope,
    continueWithAllocation,
    continueWithRegionScope,
    continueWithStore,
    createAllocation,
    archiveAllocation,
    createScope,
    transitionClue,
    reopenClue,
    addEvidence,
    entityTitle,
  } = w;
  return (
    <>
      <Modal
        title={
          entityModal
            ? `${entityModal.record ? "编辑" : "新建"}${entityTitle}`
            : undefined
        }
        open={!!entityModal}
        onCancel={closeEntityModal}
        onOk={() => entityForm.submit()}
        okText={
          entityModal?.record
            ? "保存资料"
            : entityModal?.type === "distributor"
              ? "创建经销商"
              : entityModal?.type === "region"
                ? "创建区域"
                : "创建门店"
        }
        cancelText="取消"
        confirmLoading={entitySaving}
        footer={createdDistributor || createdRegion ? null : undefined}
        forceRender
        destroyOnHidden
      >
        {createdDistributor ? (
          <Space orientation="vertical" size={16} className="w-full">
            <Alert
              type="success"
              showIcon
              title={`已创建经销商：${createdDistributor.name}`}
              description={`系统编码：${createdDistributor.code}`}
            />
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="系统编码">
                {createdDistributor.code}
              </Descriptions.Item>
              <Descriptions.Item label="联系人">
                {createdDistributor.contact_name || "未填写"}
              </Descriptions.Item>
              <Descriptions.Item label="联系电话">
                {createdDistributor.contact_phone_masked || "未填写"}
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                {createdDistributor.status === "inactive" ? "停用" : "启用"}
              </Descriptions.Item>
            </Descriptions>
            <Space wrap>
              <Button type="primary" onClick={continueWithRegion}>
                创建区域
              </Button>
              {access.canScope && (
                <Button onClick={continueWithScope}>绑定入口账号</Button>
              )}
              {access.canAllocate && (
                <Button onClick={continueWithAllocation}>登记流向</Button>
              )}
              <Button onClick={closeEntityModal}>完成</Button>
            </Space>
          </Space>
        ) : createdRegion ? (
          <Space orientation="vertical" size={16} className="w-full">
            <Alert
              type="success"
              showIcon
              title={`已创建区域：${createdRegion.name}`}
              description={`系统编码：${createdRegion.code}`}
            />
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="系统编码">
                {createdRegion.code}
              </Descriptions.Item>
              <Descriptions.Item label="省市">
                {[createdRegion.province, createdRegion.city]
                  .filter(Boolean)
                  .join(" / ") || "未设置"}
              </Descriptions.Item>
              <Descriptions.Item label="所属经销商">
                {createdRegion.distributor_name || "已绑定经销商"}
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                {createdRegion.status === "inactive" ? "停用" : "启用"}
              </Descriptions.Item>
            </Descriptions>
            <Space wrap>
              <Button type="primary" onClick={continueWithAllocation}>
                登记流向
              </Button>
              {access.canScope && (
                <Button onClick={continueWithRegionScope}>绑定入口账号</Button>
              )}
              <Button onClick={closeEntityModal}>查看区域统计</Button>
              <Button onClick={continueWithStore}>可选创建门店</Button>
              <Button onClick={closeEntityModal}>完成</Button>
            </Space>
          </Space>
        ) : (
          <Form
            form={entityForm}
            layout="vertical"
            onFinish={saveEntity}
            initialValues={{ status: "active" }}
          >
            <Form.Item
              name="name"
              label="名称"
              rules={[{ required: true, message: "请输入名称" }]}
            >
              <Input
                placeholder={
                  entityModal?.type === "distributor"
                    ? "如华东经销商"
                    : "请输入名称"
                }
              />
            </Form.Item>
            {entityModal?.record && (
              <Form.Item name="code" label="编码">
                <Input disabled />
              </Form.Item>
            )}
            {!entityModal?.record && (
              <Alert
                className="mb-4"
                type="info"
                showIcon
                title="编码将在创建后自动生成"
              />
            )}
            {entityModal?.type === "distributor" && (
              <>
                <Form.Item name="contact_name" label="联系人">
                  <Input placeholder="负责日常对接的联系人" />
                </Form.Item>
                <Form.Item
                  name="contact_phone"
                  label="联系电话"
                  rules={[
                    { pattern: /^1\d{10}$/, message: "请输入 11 位手机号" },
                  ]}
                >
                  <Input placeholder="用于渠道协同联系" />
                </Form.Item>
              </>
            )}
            {entityModal?.type === "region" && (
              <>
                <Form.Item
                  name="coverage_type"
                  label="覆盖类型"
                  rules={[{ required: true, message: "请选择覆盖类型" }]}
                >
                  <Select
                    options={[
                      { label: "城市片区", value: "city" },
                      { label: "省级片区", value: "province" },
                      { label: "大区片区", value: "multi_province" },
                    ]}
                    onChange={() => {
                      entityForm.setFieldsValue({
                        province: undefined,
                        city: undefined,
                        coverage_provinces: undefined,
                      });
                    }}
                  />
                </Form.Item>
                {(regionCoverageType || "city") !== "multi_province" && (
                  <Form.Item
                    name="province"
                    label="省份"
                    rules={[{ required: true, message: "请选择省份" }]}
                  >
                    <Select
                      showSearch
                      optionFilterProp="label"
                      placeholder="选择省份"
                      options={PROVINCE_CITY_OPTIONS.map((item) => ({
                        label: item.province,
                        value: item.province,
                      }))}
                      onChange={() =>
                        entityForm.setFieldValue("city", undefined)
                      }
                    />
                  </Form.Item>
                )}
                {(regionCoverageType || "city") === "city" && (
                  <Form.Item
                    name="city"
                    label="城市"
                    rules={[{ required: true, message: "请选择城市" }]}
                  >
                    <Select
                      showSearch
                      optionFilterProp="label"
                      placeholder="选择城市"
                      options={(
                        PROVINCE_CITY_OPTIONS.find(
                          (item) => item.province === regionProvince
                        )?.cities || []
                      ).map((city) => ({
                        label: city,
                        value: city,
                      }))}
                      disabled={!regionProvince}
                    />
                  </Form.Item>
                )}
                {regionCoverageType === "multi_province" && (
                  <Form.Item
                    name="coverage_provinces"
                    label="覆盖省份"
                    rules={[
                      {
                        required: true,
                        type: "array",
                        min: 2,
                        message: "请至少选择两个省份",
                      },
                    ]}
                  >
                    <Select
                      mode="multiple"
                      showSearch
                      optionFilterProp="label"
                      placeholder="选择大区覆盖的省份"
                      options={PROVINCE_CITY_OPTIONS.map((item) => ({
                        label: item.province,
                        value: item.province,
                      }))}
                    />
                  </Form.Item>
                )}
                <Form.Item name="distributor_id" label="所属经销商">
                  <Select allowClear options={distributorOptions} />
                </Form.Item>
              </>
            )}
            {entityModal?.type === "store" && (
              <>
                <Form.Item name="region_id" label="所属区域">
                  <Select
                    allowClear
                    options={regionOptions}
                    onChange={(regionId) => {
                      const region = regions.items.find(
                        (item) => item.id === regionId
                      );
                      if (region?.distributor_id) {
                        entityForm.setFieldValue(
                          "distributor_id",
                          region.distributor_id
                        );
                      }
                    }}
                  />
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
        )}
      </Modal>

      <Modal
        title={allocationToReassign ? "重分配流向" : "新建流向登记"}
        open={allocationOpen}
        onCancel={() => {
          setAllocationOpen(false);
          setAllocationToReassign(null);
          allocationForm.resetFields();
        }}
        onOk={() => allocationForm.submit()}
        okText="确认登记"
        cancelText="取消"
        styles={{
          body: { maxHeight: "calc(100vh - 220px)", overflowY: "auto" },
        }}
        forceRender
        destroyOnHidden
      >
        <Form
          form={allocationForm}
          layout="vertical"
          onFinish={createAllocation}
          initialValues={{ target_type: "region" }}
        >
          <Alert
            className="mb-4"
            type="info"
            showIcon
            title="记录这批已赋码货品发往哪个渠道，不开放打印或下载码包。"
          />
          <Form.Item
            name="batch_id"
            label="码批次"
            rules={[{ required: true, message: "请选择码批次" }]}
          >
            <Select
              disabled={Boolean(allocationToReassign)}
              options={batches.map((batch) => ({
                label: batch.batch_code,
                value: batch.id,
              }))}
              onChange={() => allocationForm.setFieldValue("quantity", 1)}
            />
          </Form.Item>
          {selectedAllocationBatch && (
            <Card size="small" className="mb-4">
              <Descriptions size="small" column={1}>
                <Descriptions.Item label="批次号">
                  {selectedAllocationBatch.batch_code}
                </Descriptions.Item>
                <Descriptions.Item label="产品/SKU">
                  {productSkuLabel(
                    selectedAllocationBatch.product_name,
                    selectedAllocationBatch.sku_name
                  )}
                </Descriptions.Item>
              </Descriptions>
              <Space className="mt-2" wrap>
                <Tag>总量 {selectedBatchCapacity.total}</Tag>
                <Tag color={STATUS_COLORS.processing}>
                  已登记 {selectedBatchCapacity.allocated}
                </Tag>
                <Tag color={STATUS_COLORS.success}>
                  剩余 {selectedBatchCapacity.remaining}
                </Tag>
              </Space>
            </Card>
          )}
          <Form.Item
            name="target_type"
            label="流向层级"
            rules={[{ required: true, message: "请选择流向层级" }]}
          >
            <Select
              options={[
                { label: "经销商", value: "distributor" },
                { label: "区域", value: "region" },
                { label: "门店", value: "store" },
              ]}
              onChange={(value) => {
                allocationForm.setFieldsValue({
                  distributor_id:
                    value === "distributor"
                      ? allocationForm.getFieldValue("distributor_id")
                      : undefined,
                  region_id:
                    value === "region" ? regions.items[0]?.id : undefined,
                  store_id: undefined,
                });
              }}
            />
          </Form.Item>
          {allocationTargetType === "distributor" && (
            <Form.Item
              name="distributor_id"
              label="选择经销商"
              rules={[{ required: true, message: "请选择经销商" }]}
            >
              <Select options={distributorOptions} />
            </Form.Item>
          )}
          {allocationTargetType === "region" && (
            <>
              <Form.Item
                name="region_id"
                label="选择区域"
                rules={[{ required: true, message: "请选择区域" }]}
              >
                <Select options={regionOptions} />
              </Form.Item>
              <Form.Item name="distributor_id" hidden>
                <Input />
              </Form.Item>
              {allocationDistributorName && (
                <Alert
                  className="mb-4"
                  type="info"
                  showIcon
                  title={`系统已根据区域带出经销商：${allocationDistributorName}`}
                  description="登记到区域后，经销商入口和区域入口可查看这批货品流向。"
                />
              )}
            </>
          )}
          {allocationTargetType === "store" && (
            <>
              <Form.Item
                name="store_id"
                label="选择门店"
                rules={[{ required: true, message: "请选择门店" }]}
              >
                <Select
                  options={storeOptions}
                  onChange={(storeId) => {
                    const store = stores.items.find(
                      (item) => item.id === storeId
                    );
                    allocationForm.setFieldsValue({
                      distributor_id: store?.distributor_id,
                      region_id: store?.region_id,
                    });
                  }}
                />
              </Form.Item>
              <Alert
                className="mb-4"
                type="info"
                showIcon
                title="门店入口可查看本次收货批次和扫码趋势，不开放打印或下载码包。"
              />
            </>
          )}
          <Form.Item label="登记数量" required>
            <Space.Compact className="w-full">
              <Form.Item
                name="quantity"
                noStyle
                rules={[{ required: true, message: "请输入登记数量" }]}
              >
                <InputNumber
                  aria-label="登记数量"
                  min={1}
                  max={allocationAvailableQuantity || undefined}
                  className="w-full"
                />
              </Form.Item>
              <Button
                onClick={() =>
                  allocationForm.setFieldValue(
                    "quantity",
                    allocationAvailableQuantity
                  )
                }
                disabled={!allocationAvailableQuantity}
              >
                全部登记
              </Button>
            </Space.Compact>
          </Form.Item>
          <Text type="secondary">
            可登记 1-{allocationAvailableQuantity || 0} 个
          </Text>
          <Form.Item
            name="reason"
            label={allocationToReassign ? "重分配原因" : "登记原因"}
            rules={[
              { required: true, message: "请填写原因" },
              { max: 200, message: "原因不能超过200个字" },
            ]}
          >
            <Input.TextArea rows={2} maxLength={200} showCount />
          </Form.Item>
          {allocationSummary && (
            <Alert
              className="mt-4"
              type="success"
              showIcon
              title={allocationSummary}
            />
          )}
        </Form>
      </Modal>

      <Modal
        title="归档流向登记"
        open={Boolean(allocationToArchive)}
        onCancel={() => {
          setAllocationToArchive(null);
          archiveAllocationForm.resetFields();
        }}
        onOk={() => archiveAllocationForm.submit()}
        okText="确认归档"
        okButtonProps={{ danger: true }}
        forceRender
        destroyOnHidden
      >
        <Alert
          className="mb-4"
          type="warning"
          showIcon
          title="归档会保留完整历史，并释放该批次的当前登记数量。"
        />
        <Form
          form={archiveAllocationForm}
          layout="vertical"
          onFinish={archiveAllocation}
        >
          <Form.Item
            name="reason"
            label="归档原因"
            rules={[
              { required: true, message: "请填写归档原因" },
              { max: 200, message: "原因不能超过200个字" },
            ]}
          >
            <Input.TextArea rows={3} maxLength={200} showCount />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="绑定入口账号"
        open={scopeOpen}
        onCancel={() => setScopeOpen(false)}
        onOk={() => scopeForm.submit()}
        forceRender
        destroyOnHidden
      >
        <Form form={scopeForm} layout="vertical" onFinish={createScope}>
          <Form.Item
            name="account_id"
            label="账号"
            rules={[{ required: true, message: "请选择账号" }]}
          >
            <Select
              options={accounts.map((account) => ({
                label: `${account.name} / ${account.email}`,
                value: account.id,
              }))}
            />
          </Form.Item>
          <Form.Item
            name="scope_type"
            label="入口类型"
            rules={[{ required: true, message: "请选择入口类型" }]}
          >
            <Select
              options={[
                { label: "经销商入口", value: "distributor" },
                { label: "区域入口", value: "region" },
                { label: "门店入口", value: "store" },
              ]}
            />
          </Form.Item>
          <Form.Item noStyle shouldUpdate>
            {({ getFieldValue }) =>
              getFieldValue("scope_type") === "store" ? (
                <Form.Item
                  name="store_id"
                  label="门店"
                  rules={[{ required: true, message: "请选择门店" }]}
                >
                  <Select options={storeOptions} />
                </Form.Item>
              ) : getFieldValue("scope_type") === "region" ? (
                <>
                  <Form.Item
                    name="region_id"
                    label="区域"
                    rules={[{ required: true, message: "请选择区域" }]}
                  >
                    <Select options={regionOptions} />
                  </Form.Item>
                  <Form.Item name="distributor_id" label="所属经销商">
                    <Select disabled options={distributorOptions} />
                  </Form.Item>
                </>
              ) : (
                <Form.Item
                  name="distributor_id"
                  label="经销商"
                  rules={[{ required: true, message: "请选择经销商" }]}
                >
                  <Select options={distributorOptions} />
                </Form.Item>
              )
            }
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        title="窜货线索处理"
        open={!!currentClue}
        onClose={() => {
          setCurrentClue(null);
          setInvestigation(null);
        }}
        size="large"
      >
        {currentClue && (
          <Space orientation="vertical" className="w-full" size={16}>
            <Alert
              type={currentClue.resolved ? "success" : "warning"}
              showIcon
              title={
                <Space wrap>
                  {severityTag(currentClue.severity)}
                  <Text strong>异常路径</Text>
                  <Text>{`${currentClue.expected_region || "未设置"} → ${currentClue.detected_city || "未知"}`}</Text>
                </Space>
              }
            />
            <Card title="关联货品" size="small">
              <Descriptions column={1} size="small" bordered>
                <Descriptions.Item label="码">
                  {currentClue.public_id}
                </Descriptions.Item>
                <Descriptions.Item label="产品/SKU">
                  {productSkuLabel(
                    currentClue.product_name,
                    currentClue.sku_name
                  )}
                </Descriptions.Item>
                <Descriptions.Item label="码批次">
                  {currentClue.batch_code || "未绑定批次"}
                </Descriptions.Item>
                <Descriptions.Item label="扫码时间">
                  {formatDateTime(currentClue.detected_at)}
                </Descriptions.Item>
              </Descriptions>
            </Card>
            <Card title="渠道归属" size="small">
              <Descriptions column={1} size="small" bordered>
                <Descriptions.Item label="经销商">
                  {currentClue.distributor_name || "未绑定"}
                </Descriptions.Item>
                <Descriptions.Item label="区域">
                  {currentClue.region_name ||
                    currentClue.expected_region ||
                    "未设置"}
                </Descriptions.Item>
                <Descriptions.Item label="门店">
                  {currentClue.store_name || "未识别门店"}
                </Descriptions.Item>
                <Descriptions.Item label="状态">
                  {currentClue.resolved ? "已处理" : "待处理"}
                </Descriptions.Item>
                {currentClue.resolved && (
                  <>
                    <Descriptions.Item label="处理结果">
                      {resolutionActionLabel(currentClue.resolution_action)}
                    </Descriptions.Item>
                    <Descriptions.Item label="处理时间">
                      {formatDateTime(currentClue.resolved_at)}
                    </Descriptions.Item>
                  </>
                )}
              </Descriptions>
            </Card>
            <Alert
              type="info"
              showIcon
              title="建议动作"
              description={
                currentClue.handling_recommendation ||
                "联系渠道核实货物流向，记录处理结果。"
              }
            />
            {investigation && (
              <Card title="调查时间线" size="small">
                {investigation.history.length ? (
                  <Timeline
                    items={investigation.history.map((item) => ({
                      children: (
                        <Space orientation="vertical" size={0}>
                          <Text>{`${item.from_status || "新线索"} → ${item.to_status}`}</Text>
                          <Text type="secondary">
                            {item.reason || "系统状态变更"}
                          </Text>
                          <Text type="secondary">
                            {formatDateTime(item.changed_at)}
                          </Text>
                        </Space>
                      ),
                    }))}
                  />
                ) : (
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description="尚无状态变更"
                  />
                )}
              </Card>
            )}
            {investigation && (
              <Card title="调查证据" size="small">
                <Space orientation="vertical" className="w-full" size={12}>
                  {investigation.evidence.length ? (
                    investigation.evidence.map((item) => (
                      <Alert
                        key={item.id}
                        type="info"
                        title={
                          item.description ||
                          item.file_url ||
                          item.evidence_type
                        }
                        description={formatDateTime(item.uploaded_at)}
                      />
                    ))
                  ) : (
                    <Empty
                      image={Empty.PRESENTED_IMAGE_SIMPLE}
                      description="尚未补充证据"
                    />
                  )}
                  {access.canManage && !currentClue.resolved && (
                    <Form
                      form={evidenceForm}
                      layout="vertical"
                      onFinish={addEvidence}
                    >
                      <Form.Item
                        name="evidence_type"
                        label="证据类型"
                        rules={[{ required: true }]}
                      >
                        <Select
                          options={[
                            { label: "调货单", value: "transfer" },
                            { label: "订单", value: "order" },
                            { label: "物流记录", value: "logistics" },
                            { label: "渠道说明", value: "explanation" },
                            { label: "其他", value: "other" },
                          ]}
                        />
                      </Form.Item>
                      <Form.Item
                        name="description"
                        label="证据说明"
                        rules={[{ required: true }, { max: 2000 }]}
                      >
                        <Input.TextArea rows={3} maxLength={2000} showCount />
                      </Form.Item>
                      <Button htmlType="submit">保存证据</Button>
                    </Form>
                  )}
                </Space>
              </Card>
            )}
            <Form
              form={resolveForm}
              layout="vertical"
              onFinish={transitionClue}
            >
              <Form.Item
                name="resolution_action"
                label="处理结果"
                rules={[{ required: true, message: "请选择处理结果" }]}
              >
                <Select
                  disabled={currentClue.resolved || !access.canManage}
                  options={[
                    { label: "确认窜货", value: "confirmed_diversion" },
                    { label: "误报", value: "false_positive" },
                    { label: "正常调货", value: "normal_transfer" },
                  ]}
                />
              </Form.Item>
              <Form.Item
                name="resolution_note"
                label="结论依据"
                rules={[
                  { required: true, message: "请填写结论依据" },
                  { max: 2000 },
                ]}
              >
                <Input.TextArea
                  rows={4}
                  placeholder="填写处理记录"
                  disabled={currentClue.resolved || !access.canManage}
                />
              </Form.Item>
              <Button
                type="primary"
                htmlType="submit"
                icon={<CheckOutlined />}
                disabled={
                  currentClue.resolved || !access.canManage || !investigation
                }
              >
                标记为已处理
              </Button>
              {currentClue.resolved && access.canManage && investigation && (
                <Button className="ml-2" onClick={() => void reopenClue()}>
                  重开调查
                </Button>
              )}
            </Form>
          </Space>
        )}
      </Drawer>
    </>
  );
}
