"use client";


import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { Alert, Button, Empty, Select, Typography } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { MODULE_TYPES, type ModuleConfig, type ModuleReadiness, type ModuleType } from "@/lib/page-dsl";
import { ModuleConfigForm } from "./ModuleConfigForms";
import { ModuleItem } from "./ModuleItem";

const { Text } = Typography;

export function ModuleList({
  modules,
  productId,
  moduleStatuses,
  selectedModuleId,
  onSelectModule,
  onChange,
}: {
  modules: ModuleConfig[];
  productId?: string | null;
  moduleStatuses: ModuleReadiness[];
  selectedModuleId?: string | null;
  onSelectModule: (id: string | null) => void;
  onChange: (modules: ModuleConfig[]) => void;
}) {
  const selectedModule = modules.find((module) => module.id === selectedModuleId) || null;
  const statusById = new Map(moduleStatuses.map((status) => [status.moduleId, status]));

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;

    const oldIndex = modules.findIndex((module) => module.id === active.id);
    const newIndex = modules.findIndex((module) => module.id === over.id);
    onChange(arrayMove(modules, oldIndex, newIndex));
  };

  const addModule = () => {
    const id = crypto.randomUUID();
    const newModule: ModuleConfig = { id, type: "product_hero", enabled: true, config: {} };
    onChange([newModule, ...modules]);
    onSelectModule(id);
  };

  const updateModule = (id: string, updates: Partial<ModuleConfig>) => {
    onChange(modules.map((module) => (module.id === id ? { ...module, ...updates } : module)));
  };

  const duplicateModule = (module: ModuleConfig) => {
    const id = crypto.randomUUID();
    const newModule = {
      ...module,
      id,
      config: { ...(module.config || {}) },
    };
    const sourceIndex = modules.findIndex((item) => item.id === module.id);
    const nextModules = [...modules];
    nextModules.splice(sourceIndex + 1, 0, newModule);
    onChange(nextModules);
    onSelectModule(id);
  };

  const removeModule = (id: string) => {
    const nextModules = modules.filter((module) => module.id !== id);
    onChange(nextModules);
    if (selectedModuleId === id) {
      onSelectModule(nextModules[0]?.id || null);
    }
  };

  return (
    <div className="flex h-full flex-col gap-4">
      <div>
        <div className="mb-3 flex items-center justify-between">
          <div>
            <Text strong>模块工作区</Text>
            <div className="text-xs text-text-muted">拖拽调整顺序，点击模块配置内容</div>
          </div>
          <Button size="small" icon={<PlusOutlined />} onClick={addModule}>
            添加模块
          </Button>
        </div>

        {modules.length > 0 ? (
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragEnd={handleDragEnd}
          >
            <SortableContext
              items={modules.map((module) => module.id)}
              strategy={verticalListSortingStrategy}
            >
              <div className="space-y-2">
                {modules.map((module) => (
                  <ModuleItem
                    key={module.id}
                    module={module}
                    selected={selectedModuleId === module.id}
                    readiness={statusById.get(module.id)}
                    onSelect={() => onSelectModule(module.id)}
                    onUpdate={(updates) => updateModule(module.id, updates)}
                    onRemove={() => removeModule(module.id)}
                    onDuplicate={() => duplicateModule(module)}
                  />
                ))}
              </div>
            </SortableContext>
          </DndContext>
        ) : (
          <Empty description="暂无模块，点击添加模块开始配置" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        )}
      </div>

      <div className="min-h-0 flex-1 rounded border border-border-subtle bg-bg-container p-3">
        {selectedModule ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between gap-2">
              <div>
                <Text strong>配置模块</Text>
                <div className="text-xs text-text-muted">修改后请保存草稿，预览会实时刷新</div>
              </div>
              <Select
                size="small"
                value={selectedModule.type}
                onChange={(type: ModuleType) => updateModule(selectedModule.id, { type, config: {} })}
                options={MODULE_TYPES}
                style={{ width: 150 }}
              />
            </div>
            {statusById.get(selectedModule.id)?.status === "example" ? (
              <Alert
                type="info"
                showIcon
                title={statusById.get(selectedModule.id)?.issues[0] || "当前模块会使用示例数据预览"}
              />
            ) : null}
            {statusById.get(selectedModule.id)?.status === "incomplete" ? (
              <Alert
                type="warning"
                showIcon
                title={statusById.get(selectedModule.id)?.issues[0] || "当前模块仍有配置项待完善"}
              />
            ) : null}
            <div className="rounded bg-bg-muted p-3">
              <ModuleConfigForm
                moduleType={selectedModule.type}
                productId={productId}
                config={selectedModule.config || {}}
                onChange={(config) => updateModule(selectedModule.id, { config })}
              />
            </div>
          </div>
        ) : (
          <div className="flex h-full items-center justify-center text-sm text-text-muted">
            选择一个模块后在这里配置内容
          </div>
        )}
      </div>
    </div>
  );
}
