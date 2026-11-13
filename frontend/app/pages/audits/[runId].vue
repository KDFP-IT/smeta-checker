<script setup lang="ts">
import type { RunDto, SummaryDto, RowsDto } from '~/generated/types.gen'
import type { TableColumn } from '@nuxt/ui'
import type { SortingState } from '@tanstack/vue-table'
import {
  formatFileName,
  queueColor,
  queueLabel,
  quantityWithUnit,
  runLabel,
  runStatusColor,
  truncateMiddle,
  firstSentence,
} from '~/utils/presentation'
const route = useRoute(),
  router = useRouter(),
  backend = useBackend(),
  runId = computed(() => String(route.params.runId)),
  run = ref<RunDto | null>(null),
  summary = ref<SummaryDto | null>(null),
  results = ref<RowsDto | null>(null),
  error = ref(''),
  busy = ref(true),
  cancelling = ref(false),
  resuming = ref(false),
  focus = ref('all'),
  query = ref(''),
  offset = ref(0)
const position = computed(() =>
  typeof route.query.position === 'string' && /^\d+$/.test(route.query.position)
    ? Number(route.query.position)
    : null,
)
type QueueId = 'fix' | 'review' | 'missing' | 'ok' | 'pending' | 'all'
const queues: { id: QueueId; label: string }[] = [
  { id: 'all', label: 'Все позиции' },
  { id: 'fix', label: queueLabel('fix') },
  { id: 'review', label: queueLabel('review') },
  { id: 'missing', label: queueLabel('missing') },
  { id: 'pending', label: queueLabel('pending') },
  { id: 'ok', label: queueLabel('ok') },
]
const vorFileName = computed(() => formatFileName(run.value?.vor_filename || ''))
const auditHeading = computed(() => summary.value?.project.name || 'Проверка документов')
const vorShortName = computed(() => truncateMiddle(vorFileName.value))
const runStatus = computed(() => run.value?.status || '')
function queueCount(id: QueueId) {
  if (!summary.value) return 0
  if (id === 'all') return (summary.value.ready_positions ?? []).length
  return summary.value.queues[id] ?? 0
}
const queueTabs = computed(() =>
  queues.map((item) => ({
    label: item.label,
    value: item.id,
    badge: {
      label: String(queueCount(item.id)),
      color: item.id === 'all' ? ('neutral' as const) : queueColor(item.id),
      variant: 'subtle' as const,
    },
  })),
)
function rowCell(row: RowsDto['rows'][number]) {
  // Cells map address -> value; show a stable source-row address, never a guess.
  if (typeof row.source_row !== 'number') return 'Позиция ' + row.number
  const hit = Object.keys(row.cells).find((addr) => addr.endsWith(String(row.source_row)))
  return hit || 'Позиция ' + row.number
}
type AuditRow = RowsDto['rows'][number]
const auditColumns: TableColumn<AuditRow>[] = [
  {
    id: 'position',
    accessorFn: (row: AuditRow) => row.number,
    header: 'Позиция',
    meta: { style: { th: { width: '42%' } } },
  },
  {
    id: 'status',
    accessorFn: (row: AuditRow) => row.queue,
    header: 'Статус',
    meta: { style: { th: { width: '15%' } } },
  },
  {
    id: 'result',
    accessorFn: (row: AuditRow) => row.queue,
    header: 'Результат проверки',
    meta: { style: { th: { width: '25%' } } },
  },
  {
    id: 'values',
    accessorFn: (row: AuditRow) => row.declared_quantity,
    header: 'Значения',
    meta: { style: { th: { width: '18%' } } },
  },
]
const auditSorting = ref<SortingState>([])
const pageSize = ref(10)
const auditPagination = ref({ pageIndex: 0, pageSize: 10 })
const pageCount = computed(() =>
  Math.max(1, Math.ceil((results.value?.total ?? 0) / pageSize.value)),
)
const currentPage = computed(() => Math.floor(offset.value / pageSize.value) + 1)
const pageOptions = computed(() =>
  Array.from({ length: pageCount.value }, (_, index) => ({
    label: String(index + 1),
    value: index + 1,
  })),
)
const pageSizeOptions = [
  { label: '10', value: 10 },
  { label: '25', value: 25 },
  { label: '50', value: 50 },
]
function openPosition(index: number) {
  router.replace({ query: { ...route.query, position: index } })
}
function closePosition() {
  const next = { ...route.query }
  delete next.position
  router.replace({ query: next })
}
const displayedReady = ref(0)
const newReady = computed(() =>
  results.value
    ? Math.max(0, (summary.value?.ready_positions?.length ?? 0) - displayedReady.value)
    : 0,
)
let active = true,
  timer: ReturnType<typeof setTimeout> | undefined,
  searchTimer: ReturnType<typeof setTimeout> | undefined,
  epoch = 0
async function loadRows() {
  const current = ++epoch
  busy.value = true
  try {
    const data = await backend.rows(runId.value, {
      focus: focus.value,
      q: query.value,
      offset: offset.value,
      limit: pageSize.value,
    })
    if (active && current === epoch) {
      results.value = data
      displayedReady.value = summary.value?.ready_positions?.length ?? 0
      auditPagination.value = {
        pageIndex: Math.floor(offset.value / pageSize.value),
        pageSize: pageSize.value,
      }
    }
  } catch (e) {
    if (active && current === epoch) error.value = readableError(e)
  } finally {
    if (active && current === epoch) busy.value = false
  }
}
function setPage(page: number) {
  const target = Math.min(Math.max(1, page), pageCount.value)
  offset.value = (target - 1) * pageSize.value
  loadRows()
}
function setPageSize(size: number) {
  if (size === pageSize.value) return
  pageSize.value = size
  offset.value = 0
  loadRows()
}
function scrollQueueTabs(event: WheelEvent) {
  const list = (event.currentTarget as HTMLElement).querySelector<HTMLElement>('.queue-tab-list')
  if (!list || list.scrollWidth <= list.clientWidth) return
  if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return
  const step = event.deltaY * (event.deltaMode === 1 ? 16 : 1)
  const max = list.scrollWidth - list.clientWidth
  const next = Math.min(Math.max(0, list.scrollLeft + step), max)
  // At either edge let the page scroll instead of swallowing the wheel.
  if (next === list.scrollLeft) return
  event.preventDefault()
  list.scrollLeft = next
}
async function poll() {
  clearTimeout(timer)
  error.value = ''
  try {
    const value = await backend.run(runId.value)
    if (!active) return
    const normativeChanged =
      JSON.stringify(run.value?.normative_counts) !== JSON.stringify(value.normative_counts)
    const changed =
      normativeChanged ||
      !summary.value ||
      run.value?.done !== value.done ||
      run.value?.status !== value.status
    run.value = value
    if (changed) {
      summary.value = await backend.summary(runId.value)
      if (normativeChanged || !results.value || !results.value.rows.length) await loadRows()
    }
    if (
      value.status === 'running' ||
      ['pending', 'dispatched', 'running'].some((key) => (value.normative_counts?.[key] ?? 0) > 0)
    )
      timer = setTimeout(poll, 3000)
  } catch (e) {
    if (active) {
      error.value = readableError(e) + '. Повторяем подключение…'
      timer = setTimeout(poll, 3000)
    }
  } finally {
    busy.value = false
  }
}
async function download() {
  try {
    await backend.download(
      `${backend.audit(runId.value)}/reports/word`,
      `audit-${runId.value}.docx`,
    )
  } catch (e) {
    error.value = readableError(e)
  }
}
async function resume() {
  if (resuming.value || !run.value || !['cancelled', 'failed'].includes(run.value.status)) return
  resuming.value = true
  error.value = ''
  try {
    run.value = await backend.resumeAudit(runId.value)
    await poll()
  } catch (e) {
    error.value = readableError(e)
  } finally {
    resuming.value = false
  }
}
async function cancel() {
  if (cancelling.value || !run.value || run.value.status !== 'running') return
  cancelling.value = true
  try {
    const value = await backend.cancelAudit(runId.value)
    run.value = value
    clearTimeout(timer)
    summary.value = await backend.summary(runId.value)
    await loadRows()
  } catch (e) {
    error.value = readableError(e)
  } finally {
    cancelling.value = false
    busy.value = false
  }
}
watch(focus, () => {
  offset.value = 0
  loadRows()
})
watch(query, () => {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    offset.value = 0
    loadRows()
  }, 250)
})
onMounted(async () => {
  try {
    await backend.connect()
    await poll()
  } catch (e) {
    error.value = readableError(e)
  }
})
onBeforeUnmount(() => {
  active = false
  epoch++
  clearTimeout(timer)
  clearTimeout(searchTimer)
})
</script>
<template>
  <div>
    <WorkspaceHeader active="check" wide />
    <main id="main-content" tabindex="-1" class="shell shell-wide">
      <nav v-if="summary" class="toolbar" aria-label="Проект и редакция ведомости">
        <UButton
          :to="
            summary.run.department_id
              ? `/projects/${summary.project.id}/departments/${summary.run.department_id}`
              : `/projects/${summary.project.id}`
          "
          color="neutral"
          variant="link"
          icon="i-lucide-arrow-left"
          >В проект</UButton
        >
        <UButton
          v-if="summary.documents.find((document) => document.id === summary?.run.vor_version_id)"
          :to="`/projects/${summary.project.id}/documents/${summary.documents.find((document) => document.id === summary?.run.vor_version_id)!.document_id}?version=${summary.run.vor_version_id}`"
          color="neutral"
          variant="link"
          >Редакция ведомости</UButton
        >
      </nav>
      <div class="page-heading audit-heading">
        <div class="audit-title">
          <h1>{{ auditHeading }}</h1>
          <p v-if="vorFileName" class="muted audit-eyebrow" :title="vorFileName">
            {{ vorShortName }}
          </p>
          <UBadge
            v-if="runStatus"
            :color="runStatusColor(runStatus)"
            variant="subtle"
            :aria-label="`Статус проверки: ${runLabel(runStatus)}`"
            >{{ runLabel(runStatus) }}</UBadge
          >
        </div>
        <div class="heading-actions">
          <UButton
            v-if="runStatus === 'cancelled' || runStatus === 'failed'"
            icon="i-lucide-play"
            :loading="resuming"
            @click="resume"
          >
            Продолжить обработку
          </UButton>
          <UButton
            data-testid="download-word"
            v-if="summary && (runStatus === 'done' || runStatus === 'failed')"
            variant="outline"
            icon="i-lucide-download"
            @click="download()"
          >
            Скачать Word
          </UButton>
        </div>
      </div>
      <div v-if="error" class="error-box" role="alert">
        {{ error }} <UButton variant="link" @click="poll">Повторить</UButton>
      </div>
      <p v-if="run && Object.keys(run.normative_counts ?? {}).length" role="status" class="muted">
        Нормативный слой: обработано {{ run.normative_counts?.done ?? 0 }}; выполняется
        {{ run.normative_counts?.running ?? 0 }}; ожидает
        {{ (run.normative_counts?.pending ?? 0) + (run.normative_counts?.dispatched ?? 0) }};
        прервано {{ (run.normative_counts?.failed ?? 0) + (run.normative_counts?.cancelled ?? 0) }}.
        Сопоставление и просмотр готовых позиций доступны независимо.
      </p>
      <section v-if="run?.status === 'running'" class="panel progress" role="status">
        <h2>
          {{
            {
              preparing: 'Подготавливаем документы',
              auditing: 'Проверяем позиции',
              finalizing: 'Собираем отчёты',
              done: 'Завершено',
              failed: 'Технический сбой',
            }[run.stage]
          }}
        </h2>
        <p>
          Готово {{ run.done + run.failed }}{{ run.total === null ? '' : ' из ' + run.total }}
          позиций
        </p>
        <UProgress
          :model-value="
            run.stage === 'preparing'
              ? run.preparation_total
                ? Math.floor(((run.preparation_done ?? 0) / run.preparation_total) * 100)
                : null
              : run.stage === 'finalizing' || !run.total
                ? null
                : Math.floor(((run.done + run.failed) / run.total) * 100)
          "
          :status="run.stage === 'auditing' || !!run.preparation_total"
          aria-label="Прогресс проверки"
          class="progress-bar"
        />
        <p v-if="run.stage === 'preparing'" class="muted">
          Извлекаем текст и таблицы PDF.
          <span v-if="run.preparation_total"
            >Страниц текущего PDF: {{ run.preparation_done }} из {{ run.preparation_total }}.</span
          >
          Счётчик позиций начнёт расти после подготовки документов.
        </p>
        <p v-else-if="run.stage === 'auditing'" class="muted">
          Сейчас проверяется: {{ run.running }}. В очереди: {{ run.queued }}.
        </p>
        <p class="muted">Можно закрыть страницу и вернуться к проверке через историю.</p>
        <div class="toolbar">
          <UButton
            color="neutral"
            variant="outline"
            :to="
              run.department_id
                ? `/projects/${run.project_id}/departments/${run.department_id}`
                : `/projects/${run.project_id}`
            "
          >
            К проекту
          </UButton>
          <UButton color="error" variant="outline" :loading="cancelling" @click="cancel">
            Остановить проверку
          </UButton>
        </div>
      </section>
      <p v-if="run?.status === 'failed'" class="error-box">
        Запуск завершился с техническим сбоем. Сохранённые результаты доступны ниже; непроверенные
        позиции не подтверждены.
      </p>
      <p v-if="run?.status === 'cancelled'" class="error-box">
        Проверка остановлена. Готовые результаты и сохранённые исправления доступны ниже.
      </p>
      <p v-if="runStatus === 'cancelled' || runStatus === 'failed'" class="muted">
        Продолжение обработает оставшиеся позиции. Готовые результаты и ваши основания сохранятся.
      </p>
      <template v-if="summary">
        <div class="toolbar results-bar">
          <div class="queue-tabs" @wheel="scrollQueueTabs">
            <UTabs
              :model-value="focus"
              :items="queueTabs"
              :content="false"
              color="neutral"
              activation-mode="manual"
              aria-label="Статус позиций"
              :ui="{
                list: 'queue-tab-list',
                indicator: 'bg-default shadow-sm',
                trigger: 'queue-tab-trigger data-[state=active]:text-highlighted',
              }"
              @update:model-value="(value) => (focus = String(value))"
            />
          </div>
          <UButton v-if="newReady" size="sm" variant="soft" @click="loadRows"
            >Новые результаты · {{ newReady }}</UButton
          >
          <UInput
            v-model="query"
            class="search"
            placeholder="Работа, номер или ячейка"
            aria-label="Поиск позиций"
          />
        </div>
        <div class="panel table-wrap" :aria-busy="busy">
          <UTable
            v-if="results?.rows?.length"
            :data="results.rows"
            v-model:sorting="auditSorting"
            v-model:pagination="auditPagination"
            :pagination-options="{ manualPagination: true, rowCount: results?.total ?? 0 }"
            :columns="auditColumns"
            :ui="{ base: 'table-fixed w-full' }"
            class="results"
          >
            <template #position-cell="{ row }">
              <div>
                <strong class="row-name"
                  >{{ row.original.number }} · {{ row.original.name }}</strong
                >
                <p class="muted">{{ row.original.sheet || 'CSV' }} · {{ rowCell(row.original) }}</p>
              </div>
            </template>
            <template #status-cell="{ row }">
              <UBadge
                :color="queueColor(row.original.queue)"
                variant="subtle"
                class="max-w-full whitespace-normal"
              >
                {{ queueLabel(row.original.queue) }}
              </UBadge>
            </template>
            <template #result-cell="{ row }">
              <p class="row-problem">{{ firstSentence(row.original.problem) }}</p>
            </template>
            <template #values-cell="{ row }">
              <div class="numbers">
                <p>
                  <span class="muted">ВОР </span
                  ><strong>{{
                    quantityWithUnit(row.original.declared_quantity, row.original.unit)
                  }}</strong>
                </p>
                <p class="muted">
                  ПД {{ quantityWithUnit(row.original.found_quantity, row.original.found_unit) }}
                </p>
                <UButton
                  variant="link"
                  size="sm"
                  class="open-position"
                  @click="openPosition(row.original.position_index)"
                >
                  Открыть позицию
                </UButton>
              </div>
            </template>
          </UTable>
          <p v-if="!results?.rows.length && !busy" class="empty">По этому фильтру позиций нет.</p>
          <div v-if="results?.total" class="table-footer">
            <div class="page-size">
              <span class="muted">Строк на странице</span>
              <USelect
                :model-value="pageSize"
                :items="pageSizeOptions"
                size="sm"
                class="page-size-select"
                aria-label="Строк на странице"
                @update:model-value="(value) => setPageSize(Number(value))"
              />
            </div>
            <div class="pager">
              <UButton
                icon="i-lucide-chevron-left"
                color="neutral"
                variant="ghost"
                size="sm"
                aria-label="Предыдущая страница"
                :disabled="currentPage <= 1"
                @click="setPage(currentPage - 1)"
              />
              <USelect
                :model-value="currentPage"
                :items="pageOptions"
                size="sm"
                class="page-select"
                aria-label="Номер страницы"
                @update:model-value="(value) => setPage(Number(value))"
              />
              <span class="muted">из {{ pageCount }}</span>
              <UButton
                icon="i-lucide-chevron-right"
                color="neutral"
                variant="ghost"
                size="sm"
                aria-label="Следующая страница"
                :disabled="currentPage >= pageCount"
                @click="setPage(currentPage + 1)"
              />
            </div>
          </div>
        </div>
      </template>
      <AuditWorkspace
        v-if="position !== null && summary"
        :key="runId"
        :run-id="runId"
        :position-index="position"
        :position-count="summary.run.total ?? 0"
        :ready-positions="summary.ready_positions"
        :documents="summary.documents"
        @close="closePosition"
        @navigate="openPosition"
      />
    </main>
  </div>
</template>
<style scoped>
.audit-heading {
  align-items: flex-start;
}
.audit-title {
  min-width: 0;
  max-width: 68ch;
  display: grid;
  gap: 8px;
  justify-items: start;
}
.audit-eyebrow {
  margin: 0;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.audit-title h1 {
  margin: 0;
}
@media (max-width: 700px) {
  .audit-eyebrow {
    white-space: normal;
    overflow-wrap: anywhere;
  }
}
.heading-actions {
  display: flex;
  gap: 12px;
  align-items: center;
}
.progress {
  padding: 24px;
  margin: 24px 0;
}
.progress-bar {
  margin: 16px 0;
}
.results-bar {
  margin: 20px 0 12px;
  align-items: center;
  gap: 12px;
}
.queue-tabs {
  min-width: 0;
  flex: 1 1 auto;
}
.queue-tabs :deep(.queue-tab-list) {
  width: auto;
  max-width: 100%;
  overflow-x: auto;
  overflow-y: hidden;
  scrollbar-width: thin;
  scrollbar-color: var(--ui-border-accented) transparent;
}
.queue-tabs :deep(.queue-tab-list)::-webkit-scrollbar {
  height: 6px;
}
.queue-tabs :deep(.queue-tab-list)::-webkit-scrollbar-track {
  background: transparent;
}
.queue-tabs :deep(.queue-tab-list)::-webkit-scrollbar-thumb {
  background: var(--ui-border-accented);
  border-radius: 999px;
}
.queue-tabs :deep(.queue-tab-trigger) {
  flex: 0 0 auto;
  min-height: 36px;
  padding: 4px 12px;
  font-size: 14px;
  white-space: nowrap;
}
.search {
  flex: 1 1 240px;
  min-width: min(100%, 240px);
}
.table-wrap {
  min-width: 0;
  width: 100%;
  overflow: hidden;
}
.table-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  padding: 12px 16px;
  border-top: 1px solid var(--ui-border-muted);
}
.page-size,
.pager {
  display: flex;
  align-items: center;
  gap: 8px;
}
.page-size {
  font-size: 14px;
}
.page-size-select {
  width: 88px;
}
.page-select {
  width: 76px;
}
.results :deep(table) {
  table-layout: fixed;
  width: 100%;
}
.results :deep(th),
.results :deep(td) {
  white-space: normal;
  overflow-wrap: anywhere;
  min-width: 0;
}
.results :deep(th:nth-child(1)) {
  width: 42%;
}
.results :deep(th:nth-child(2)) {
  width: 15%;
}
.results :deep(th:nth-child(3)) {
  width: 25%;
}
.results :deep(th:nth-child(4)) {
  width: 18%;
}
.open-position {
  margin-top: 8px;
  padding-left: 0;
}
.results :deep(button) {
  white-space: normal;
  max-width: 100%;
}
.row-name {
  display: block;
  color: var(--ui-text-highlighted);
}
.row-problem {
  margin-top: 0;
  color: var(--ui-text-highlighted);
  font-weight: 600;
}
.numbers {
  font-variant-numeric: tabular-nums;
  white-space: normal;
}
.row-action {
  text-align: right;
  white-space: nowrap;
}
@media (max-width: 700px) {
  .results :deep(thead) {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }
  .results :deep(table),
  .results :deep(tbody) {
    display: block;
  }
  .results :deep(tr) {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 16px 12px;
    padding: 20px 16px;
  }
  .results :deep(td) {
    display: block;
    padding: 0;
    border: 0;
  }
  .results :deep(td:nth-child(1)),
  .results :deep(td:nth-child(2)),
  .results :deep(td:nth-child(3)),
  .results :deep(td:nth-child(4)) {
    grid-column: 1 / -1;
  }
  .results :deep(tbody tr + tr) {
    border-top: 1px solid var(--ui-border);
  }
  .row-action {
    align-self: end;
    text-align: end;
  }
}
</style>
