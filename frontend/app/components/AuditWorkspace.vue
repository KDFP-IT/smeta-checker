<script setup lang="ts">
import type { SplitterItem } from '@nuxt/ui'
import type {
  WorkspaceDto,
  VersionDto,
  StructureDto,
  StructureNodeDto,
} from '~/generated/types.gen'
import {
  queueColor,
  queueLabel,
  quantityDelta,
  quantityWithUnit,
  unitText,
} from '~/utils/presentation'

const props = defineProps<{
  runId: string
  positionIndex: number
  documents: VersionDto[]
  positionCount?: number
  readyPositions?: number[]
}>()
const emit = defineEmits<{ close: []; navigate: [index: number] }>()
const backend = useBackend()
const open = ref(true)
const workspace = ref<WorkspaceDto | null>(null)
const busy = ref(true)
const error = ref('')
const version = ref('')
const page = ref(1)
const structure = ref<StructureDto | null>(null)
const structureError = ref('')
const selectedNode = ref('')
const outlineOpen = ref(false)
const search = ref('')
const searchPages = ref<number[]>([])
const mobileTab = ref<'vor' | 'pdf'>('vor')
const sourceTab = ref<'pd' | 'vor'>('pd')
const sourceItems = [
  { label: 'ПД', value: 'pd' },
  { label: 'ВОР', value: 'vor' },
]
const isNarrow = ref(false)
const pdfPanel = ref<{ find: (query: string) => Promise<number[]> }>()
const focusedEvidence = computed(
  () =>
    workspace.value?.evidence.find((item) => (item.region?.highlights?.length || 0) > 0) ||
    workspace.value?.evidence[0] ||
    null,
)
const evidenceQuote = computed(() => focusedEvidence.value?.quote || '')
const evidenceRegion = computed(() => focusedEvidence.value?.region || null)
const dossierItems: SplitterItem[] = [
  { slot: 'pdf', minSize: 30, defaultSize: 62 },
  { slot: 'dossier', minSize: 25, defaultSize: 38 },
]
const pdfs = computed(() => props.documents.filter((document) => document.kind === 'pdf'))
const previousReady = computed(() =>
  props.readyPositions
    ? (props.readyPositions.filter((i) => i < props.positionIndex).toSorted((a, b) => b - a)[0] ??
      null)
    : props.positionIndex > 0
      ? props.positionIndex - 1
      : null,
)
const nextReady = computed(() =>
  props.readyPositions
    ? (props.readyPositions.filter((i) => i > props.positionIndex).toSorted((a, b) => a - b)[0] ??
      null)
    : typeof props.positionCount === 'number' && props.positionIndex + 1 < props.positionCount
      ? props.positionIndex + 1
      : null,
)
const headerDescription = computed(() =>
  workspace.value
    ? `${workspace.value.row.number} · ${workspace.value.row.name}`
    : 'Открываем позицию…',
)
const foundSummary = computed(() => {
  const row = workspace.value?.row
  if (!row?.found_quantity) return 'нет данных'
  return quantityWithUnit(row.found_quantity, row.found_unit)
})
const compareDelta = computed(() => {
  const row = workspace.value?.row
  const declared = unitText(row?.unit).toLocaleLowerCase('ru-RU')
  const found = unitText(row?.found_unit).toLocaleLowerCase('ru-RU')
  return declared && found && declared === found
    ? quantityDelta(row?.declared_quantity, row?.found_quantity)
    : null
})
const verdictDirection = computed(() => {
  const row = workspace.value?.row
  const delta = compareDelta.value
  if (!row || row.queue !== 'fix' || /в самой ВОР/i.test(row.problem) || !delta || delta === '±0')
    return ''
  const amount = delta.slice(1) + (unitText(row.unit) ? ` ${unitText(row.unit)}` : '')
  return delta.startsWith('+') ? `ПД превышает ВОР на ${amount}` : `ВОР превышает ПД на ${amount}`
})
const problemText = computed(() => String(workspace.value?.row.problem || '').trim())
const problemSummary = computed(() => {
  const text = problemText.value
  if (!text) return 'Результат проверки'
  const first = text.split(/(?<=[.!?])\s+/)[0] || text
  return first.length > 180 ? `${first.slice(0, 177).trimEnd()}…` : first
})
const sourceCells = computed(() =>
  Object.entries(workspace.value?.row.cells || {}).filter(([address]) =>
    /^[A-Z]{1,3}\d{1,4}$/.test(address),
  ),
)
const evidenceCells = computed(() =>
  issueCells.value.length
    ? sourceCells.value.filter(([address]) => issueCells.value.includes(address))
    : sourceCells.value,
)
const issueCells = computed(() => [
  ...new Set(problemText.value.match(/\b[A-Z]{1,3}\d{1,4}\b/g) || []),
])
const issueTitle = computed(() => {
  const row = workspace.value?.row
  if (!row) return 'Открываем позицию…'
  if (row.queue === 'fix' && row.assessment?.relationships?.state === 'possible_mismatch')
    return 'Внутреннее противоречие ВОР'
  if (row.queue === 'fix') return 'Расхождение с ПД'
  if (row.queue === 'review') return 'Нужна ручная проверка'
  if (row.queue === 'ok') return 'Соответствие ПД подтверждено'
  return queueLabel(row.queue)
})
const issueLocation = computed(() => {
  const row = workspace.value?.row
  if (!row) return '—'
  const sheet = row.sheet || workspace.value?.position.position_source?.sheet
  const rowNumber = row.source_row
  const cells = issueCells.value.length ? ` · ${issueCells.value.join(', ')}` : ''
  return [sheet, rowNumber ? `строка ${rowNumber}` : ''].filter(Boolean).join(' · ') + cells
})
const issueWhy = computed(() => {
  const row = workspace.value?.row
  const relation = row?.assessment?.relationships?.obligations?.find(
    (item) => item.state === 'possible_mismatch',
  )
  const reason = relation?.reason || problemText.value
  if (/Разные исходные числа/i.test(reason)) {
    return `Формула D36 и поясняющий расчёт E36 используют разные исходные числа.`
  }
  return reason.split(/(?<=[.!?])\s+/)[0] || reason
})
const issueAction = computed(() => {
  const match = problemText.value.match(/Нужно[^.]*\./)
  return match?.[0] || workspace.value?.row.action || 'Проверьте исходные данные позиции.'
})
const isInternalVOR = computed(() => {
  const row = workspace.value?.row
  return Boolean(
    row &&
    row.queue === 'fix' &&
    row.assessment?.relationships?.state === 'possible_mismatch' &&
    !row.found_quantity,
  )
})
type ComparisonSide = {
  address: string
  formula: string
  result: number | null
  source: string
  operands: number[]
}
const selectedCell = ref('')
const comparison = computed(() => {
  const row = workspace.value?.row
  if (!row || !issueCells.value.length) return null
  const sheetCells = (workspace.value?.excel?.rows || []).flatMap((item) => item.cells)
  const sides = issueCells.value.slice(0, 2).map((address): ComparisonSide => {
    const raw = String(
      sheetCells.find((item) => item.coordinate === address)?.value || row.cells?.[address] || '',
    )
    const formula = raw.replace(/^=/, '')
    const operands = (formula.match(/\d+(?:[.,]\d+)?/g) || [])
      .map((item) => Number(item.replace(',', '.')))
      .filter((item) => Number.isFinite(item))
    return {
      address,
      formula: formula.replace(/\./g, ','),
      result: evaluateFormula(formula),
      source: operands.length ? String(operands[0]).replace(/\./g, ',') : '—',
      operands,
    }
  })
  const first = sides[0]?.result
  const second = sides[1]?.result
  const difference = first != null && second != null ? Math.abs(second - first) : null
  const unit = row.unit || ''
  return {
    sides,
    difference,
    unit,
    kilograms: unit === 'т' && difference != null ? difference * 1000 : null,
    coefficient:
      String(workspace.value?.position.comment || '').match(/\d+(?:[.,]\d+)?/)?.[0] || '',
  }
})

function evaluateFormula(formula: string): number | null {
  const text = formula.replace(/,/g, '.').replace(/\s/g, '')
  if (!text || !/^[\d.+\-*/()]+$/.test(text)) return null
  const tokens = text.match(/\d+(?:\.\d+)?|[()+\-*/]/g)
  if (!tokens) return null
  let index = 0
  const expression = (): number | null => {
    let value = term()
    while (tokens[index] === '+' || tokens[index] === '-') {
      const op = tokens[index++]
      const right = term()
      if (value == null || right == null) return null
      value = op === '+' ? value + right : value - right
    }
    return value
  }
  const term = (): number | null => {
    let value = factor()
    while (tokens[index] === '*' || tokens[index] === '/') {
      const op = tokens[index++]
      const right = factor()
      if (value == null || right == null) return null
      value = op === '*' ? value * right : right === 0 ? null : value / right
    }
    return value
  }
  const factor = (): number | null => {
    const token = tokens[index++]
    if (token === '(') {
      const value = expression()
      if (tokens[index++] !== ')') return null
      return value
    }
    const value = Number(token)
    return Number.isFinite(value) ? value : null
  }
  const result = expression()
  return index === tokens.length && result != null && Number.isFinite(result) ? result : null
}

function formatNumber(value: number | null) {
  return value == null ? '—' : value.toLocaleString('ru-RU', { maximumFractionDigits: 6 })
}

function selectCell(address: string) {
  selectedCell.value = address
  nextTick(() => {
    document.querySelector<HTMLElement>(`[data-cell="${address}"]`)?.scrollIntoView({
      block: 'center',
      inline: 'center',
    })
  })
}

const issueEvidence = computed(() => {
  const row = workspace.value?.row
  if (!row) return '—'
  if (isInternalVOR.value) {
    return 'Расчётом ВОР: значения D36 и E36 расходятся.'
  }
  const projectConfirmed = row.assessment?.project?.state === 'confirmed'
  if (projectConfirmed && row.found_quantity) {
    return `Количество ПД: ${quantityWithUnit(row.found_quantity, row.found_unit)}. Состав работ и единица подтверждены.`
  }
  if (projectConfirmed)
    return 'Состав работ и единица подтверждены ПД; количество требует ручной проверки.'
  return 'ПД: подтверждение не найдено.'
})

let active = true
let positionEpoch = 0
let structureEpoch = 0
let mediaQuery: MediaQueryList | null = null
function onMediaChange(event: MediaQueryListEvent) {
  isNarrow.value = event.matches
}
async function loadStructure() {
  const current = ++structureEpoch
  structure.value = null
  structureError.value = ''
  if (!version.value) return
  try {
    const result = await backend.structure(version.value)
    if (active && current === structureEpoch) structure.value = result
  } catch (e) {
    if (active && current === structureEpoch) structureError.value = readableError(e)
  }
}
async function loadPosition() {
  const current = ++positionEpoch
  busy.value = true
  workspace.value = null
  error.value = ''
  searchPages.value = []
  selectedNode.value = ''
  try {
    const result = await backend.workspace(props.runId, props.positionIndex)
    if (!active || current !== positionEpoch) return
    workspace.value = result
    selectedCell.value = String(result.row.problem || '').match(/\b[A-Z]{1,3}\d{1,4}\b/)?.[0] || ''
    sourceTab.value =
      result.row.queue === 'fix' &&
      result.row.assessment?.relationships?.state === 'possible_mismatch' &&
      !result.row.found_quantity
        ? 'vor'
        : 'pd'
    const focus =
      result.evidence.find((item) => (item.region?.highlights?.length || 0) > 0) ||
      result.evidence[0]
    version.value = focus?.version_id || pdfs.value[0]?.id || ''
    page.value = focus?.page || 1
  } catch (e) {
    if (active && current === positionEpoch) error.value = readableError(e)
  } finally {
    if (active && current === positionEpoch) busy.value = false
  }
}
function selectNode(node: StructureNodeDto) {
  page.value = node.physical_start
  selectedNode.value = node.id
  outlineOpen.value = false
}
function changePage(value: number) {
  page.value = value
  selectedNode.value = ''
}
async function find() {
  if (pdfPanel.value) searchPages.value = await pdfPanel.value.find(search.value)
}
async function downloadSource() {
  if (!workspace.value) return
  try {
    await backend.download(
      backend.contentUrl(workspace.value.vor_version_id) + '?download=true',
      'ВОР.' + (workspace.value.excel ? 'xlsx' : 'csv'),
    )
  } catch (e) {
    error.value = readableError(e)
  }
}
watch(version, loadStructure)
watch(() => props.positionIndex, loadPosition)
watch(
  () => props.readyPositions,
  (ready) => {
    if (!workspace.value && !busy.value && ready?.includes(props.positionIndex)) void loadPosition()
  },
)
watch(open, (value) => {
  if (!value) emit('close')
})
onMounted(async () => {
  mediaQuery = window.matchMedia('(max-width: 720px)')
  isNarrow.value = mediaQuery.matches
  mediaQuery.addEventListener('change', onMediaChange)
  await loadPosition()
})
onBeforeUnmount(() => {
  active = false
  positionEpoch++
  structureEpoch++
  mediaQuery?.removeEventListener('change', onMediaChange)
})
</script>

<template>
  <UModal v-model:open="open" fullscreen title="Сверка ВОР с PDF" :description="headerDescription">
    <template #content>
      <div class="aw-dossier">
        <header class="aw-topbar">
          <div class="aw-nav">
            <UButton
              data-testid="prev-position"
              color="neutral"
              variant="ghost"
              icon="i-lucide-chevron-left"
              aria-label="Предыдущая позиция"
              :disabled="previousReady === null"
              @click="previousReady !== null && emit('navigate', previousReady)"
            />
            <UButton
              color="neutral"
              data-testid="next-position"
              icon="i-lucide-chevron-right"
              aria-label="Следующая позиция"
              :disabled="nextReady === null"
              @click="nextReady !== null && emit('navigate', nextReady)"
            />
          </div>
          <div class="aw-heading">
            <p class="aw-position">
              Позиция {{ positionIndex + 1
              }}{{ typeof positionCount === 'number' ? ` из ${positionCount}` : '' }} ·
              {{ workspace?.row.name ?? 'Открываем позицию…' }}
            </p>
            <div v-if="workspace" class="aw-header-summary" aria-live="polite">
              <UBadge
                :label="queueLabel(workspace.row.queue)"
                :color="queueColor(workspace.row.queue)"
                variant="soft"
                size="sm"
              />
              <span
                >ВОР:
                <strong>{{
                  quantityWithUnit(workspace.row.declared_quantity, workspace.row.unit)
                }}</strong></span
              >
              <template v-if="!isInternalVOR">
                <span aria-hidden="true">{{
                  compareDelta === null ? '·' : compareDelta === '±0' ? '=' : '≠'
                }}</span>
                <span
                  >ПД: <strong>{{ foundSummary }}</strong></span
                >
                <span v-if="compareDelta && compareDelta !== '±0'"
                  >Разница: {{ compareDelta }} {{ workspace.row.unit || '' }}</span
                >
              </template>
            </div>
          </div>
          <UButton
            color="neutral"
            variant="ghost"
            size="sm"
            icon="i-lucide-x"
            aria-label="Закрыть сверку (Esc)"
            @click="open = false"
            >Закрыть</UButton
          >
        </header>
        <div v-if="busy && !workspace" class="aw-loading" role="status">Открываем позицию…</div>
        <p v-if="error" class="error-box" role="alert">{{ error }}</p>
        <template v-if="workspace">
          <div class="aw-tabs" role="group" aria-label="Выбор панели">
            <UButton
              color="neutral"
              :variant="mobileTab === 'vor' ? 'solid' : 'outline'"
              :aria-pressed="mobileTab === 'vor'"
              @click="mobileTab = 'vor'"
              >Данные ВОР</UButton
            >
            <UButton
              color="neutral"
              :variant="mobileTab === 'pdf' ? 'solid' : 'outline'"
              :aria-pressed="mobileTab === 'pdf'"
              @click="mobileTab = 'pdf'"
              >PDF</UButton
            >
          </div>
          <main class="aw-main" :data-tab="mobileTab">
            <USplitter
              id="workspace-dossier-splitter"
              auto-save-id="workspace-dossier-splitter"
              :items="dossierItems"
              :orientation="isNarrow ? 'vertical' : 'horizontal'"
              class="aw-splitter"
            >
              <template #pdf>
                <div class="aw-pdf">
                  <UTabs
                    :model-value="sourceTab"
                    :items="sourceItems"
                    :content="false"
                    variant="pill"
                    color="neutral"
                    size="sm"
                    aria-label="Источник"
                    class="aw-source-tabs"
                    :ui="{ list: 'w-fit justify-start' }"
                    @update:model-value="(value) => (sourceTab = String(value) as 'pd' | 'vor')"
                  />
                  <template v-if="sourceTab === 'pd'">
                    <div class="aw-pdf-toolbar">
                      <UButton
                        label="Оглавление"
                        icon="i-lucide-list-tree"
                        color="neutral"
                        variant="outline"
                        :aria-expanded="outlineOpen"
                        aria-controls="pdf-outline"
                        @click="outlineOpen = !outlineOpen"
                      />
                      <USelect
                        v-model="version"
                        :items="pdfs.map((v) => ({ label: v.filename, value: v.id }))"
                        aria-label="Проектный PDF"
                        class="aw-document-select"
                      />
                      <div v-if="outlineOpen" id="pdf-outline" class="aw-outline-panel">
                        <div class="aw-outline-header">
                          <strong>Оглавление ПД</strong
                          ><UButton
                            icon="i-lucide-x"
                            aria-label="Закрыть оглавление"
                            color="neutral"
                            variant="ghost"
                            @click="outlineOpen = false"
                          />
                        </div>
                        <OutlineTree
                          :data="structure"
                          :page="page"
                          :selected-id="selectedNode"
                          :error="structureError"
                          @select="selectNode"
                        />
                      </div>
                      <UInput
                        v-model="search"
                        placeholder="Текст в PDF"
                        aria-label="Поиск в PDF, Enter для поиска"
                        size="sm"
                        icon="i-lucide-search"
                        @keyup.enter="find"
                      />
                    </div>
                    <p v-if="searchPages.length" class="muted">
                      Найдено на страницах: {{ searchPages.join(', ') }}
                    </p>
                    <PdfPanel
                      v-if="version"
                      ref="pdfPanel"
                      :key="version"
                      :source-url="backend.contentUrl(version)"
                      :page="page"
                      :quote="evidenceQuote"
                      :region="evidenceRegion"
                      @page="changePage"
                    />
                    <p
                      v-if="version && !evidenceRegion?.highlights?.length"
                      class="aw-evidence-note"
                    >
                      Точная область подтверждения в PDF не найдена.
                    </p>
                    <p v-else-if="!version" class="empty">Для этого аудита PDF не выбран.</p>
                  </template>
                  <div v-else class="aw-vor-source">
                    <div class="aw-vor-head">
                      <h2 class="aw-section-title">
                        {{
                          workspace.excel?.sheet ||
                          workspace.position.position_source?.sheet ||
                          'ВОР'
                        }}
                        <template v-if="workspace.row.source_row">
                          · строка {{ workspace.row.source_row }}
                        </template>
                      </h2>
                      <UButton
                        icon="i-lucide-download"
                        color="neutral"
                        variant="ghost"
                        size="xs"
                        aria-label="Скачать исходную ВОР"
                        title="Скачать исходную ВОР"
                        @click="downloadSource"
                      />
                    </div>
                    <div v-if="workspace.excel" class="aw-sheet-scroll">
                      <table class="aw-sheet-grid">
                        <thead>
                          <tr>
                            <th class="aw-sheet-corner" scope="col"></th>
                            <th v-for="column in workspace.excel.columns" :key="column" scope="col">
                              {{ column }}
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr
                            v-for="row in workspace.excel.rows"
                            :key="row.number"
                            :class="{ 'is-selected-row': row.selected }"
                          >
                            <th scope="row">{{ row.number }}</th>
                            <td
                              v-for="cell in row.cells"
                              :key="cell.coordinate"
                              :colspan="cell.column_span"
                              :rowspan="cell.row_span"
                              :class="{
                                'is-source': cell.highlight,
                                'is-error': issueCells.includes(cell.coordinate),
                                'is-selected-cell': selectedCell === cell.coordinate,
                              }"
                              :title="cell.coordinate"
                              :data-cell="cell.coordinate"
                              @click="selectCell(cell.coordinate)"
                            >
                              {{ cell.value }}
                            </td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                    <p v-else class="empty">Фрагмент ВОР недоступен.</p>
                  </div>
                </div>
              </template>
              <template #dossier>
                <div class="aw-dossier-pane excel-pane">
                  <section class="aw-result" aria-label="Результат проверки">
                    <p class="aw-verdict-kicker">{{ queueLabel(workspace.row.queue) }}</p>
                    <h1 class="aw-verdict-title">{{ issueTitle }}</h1>
                    <div class="aw-answers">
                      <div class="aw-answer">
                        <h2>Что обнаружено</h2>
                        <p>{{ problemSummary }}</p>
                      </div>
                      <div class="aw-answer">
                        <h2>Где именно</h2>
                        <p>{{ issueLocation }}</p>
                      </div>
                      <div class="aw-answer">
                        <h2>Почему это ошибка</h2>
                        <p>{{ issueWhy }}</p>
                      </div>
                      <div class="aw-answer">
                        <h2>Чем подтверждается</h2>
                        <p>{{ issueEvidence }}</p>
                        <div v-if="comparison" class="aw-comparison">
                          <div class="aw-compare-sides">
                            <button
                              v-for="side in comparison.sides"
                              :key="side.address"
                              type="button"
                              class="aw-compare-side"
                              :class="{ 'is-selected': selectedCell === side.address }"
                              @click="selectCell(side.address)"
                            >
                              <div class="aw-compare-head">
                                <code>{{ side.address }}</code>
                                <strong
                                  >{{ formatNumber(side.result) }} {{ comparison.unit }}</strong
                                >
                              </div>
                              <div class="aw-compare-formula">
                                <code>{{ side.formula || '—' }}</code>
                              </div>
                              <div class="aw-compare-source">
                                Исходное значение: <strong>{{ side.source }}</strong>
                              </div>
                            </button>
                          </div>
                          <div v-if="comparison.difference != null" class="aw-compare-diff">
                            <span>Разница результатов</span>
                            <strong
                              >{{ formatNumber(comparison.difference) }} {{ comparison.unit
                              }}<template v-if="comparison.kilograms != null">
                                ({{ formatNumber(comparison.kilograms) }} кг)</template
                              ></strong
                            >
                          </div>
                          <p v-if="comparison.coefficient" class="aw-compare-note">
                            Одинаковый расход: {{ comparison.coefficient }} л/м². Различаются
                            исходные значения.
                          </p>
                        </div>
                        <div v-else-if="evidenceCells.length" class="aw-evidence-cells">
                          <span
                            v-for="[address, value] in evidenceCells"
                            :key="address"
                            class="aw-evidence-cell"
                            :class="{ 'is-error': issueCells.includes(address) }"
                          >
                            <code>{{ address }}</code>
                            <span>{{ value }}</span>
                          </span>
                        </div>
                      </div>
                      <div class="aw-answer">
                        <h2>Что проверить</h2>
                        <p>{{ issueAction }}</p>
                      </div>
                    </div>
                  </section>
                  <section
                    v-if="workspace.position.comment && !comparison"
                    :key="positionIndex"
                    class="aw-note-card"
                    aria-label="Примечание"
                  >
                    <h2 class="aw-section-title">Примечание позиции</h2>
                    <p>{{ workspace.position.comment }}</p>
                  </section>
                </div>
              </template>
            </USplitter>
          </main>
        </template>
      </div>
    </template>
  </UModal>
</template>

<style scoped>
.aw-dossier {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  overflow-x: clip;
  background: var(--ui-bg-muted);
  color: var(--ui-text);
}
.aw-topbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: var(--ui-bg-elevated);
  border-bottom: 1px solid var(--ui-border);
}
.aw-nav {
  display: flex;
}
.aw-heading {
  min-width: 0;
  flex: 1;
}
.aw-position {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--ui-text-highlighted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.aw-header-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 12px;
  font-size: 14px;
  font-variant-numeric: tabular-nums;
}
.aw-loading {
  padding: 24px;
}
.aw-tabs {
  display: none;
  gap: 8px;
  padding: 12px 16px 0;
}
.aw-main {
  flex: 1;
  min-height: 0;
  padding: 12px 16px 16px;
}
.aw-splitter {
  height: 100%;
  min-height: 0;
}
.aw-splitter :deep([role='separator']) {
  width: 1px;
  background: var(--ui-border);
  margin-inline: 6px;
}
.aw-splitter :deep([role='separator']:hover),
.aw-splitter :deep([role='separator']:focus-visible) {
  background: var(--ui-primary);
}
.aw-pdf {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  width: 100%;
  height: 100%;
  min-height: 0;
}
.aw-pdf-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border: 1px solid var(--ui-border);
  border-radius: 8px;
  background: var(--ui-bg-elevated);
}
.aw-document-select {
  max-width: 280px;
  min-width: 0;
}
.aw-outline-panel {
  position: absolute;
  z-index: 11;
  top: 52px;
  left: 0;
  bottom: 0;
  width: min(340px, 100%);
  border: 1px solid var(--ui-border);
  background: var(--ui-bg-elevated);
  padding: 12px;
  overflow: auto;
  box-shadow: var(--app-shadow-overlay);
}
.aw-outline-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.aw-dossier-pane {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 16px;
  min-width: 0;
  width: 100%;
  height: 100%;
  min-height: 0;
  overflow: auto;
}
.aw-result,
.aw-note-card {
  padding: 16px;
  background: var(--ui-bg-elevated);
  border: 1px solid var(--ui-border);
  border-radius: 12px;
  clip-path: polygon(
    0 0,
    calc(100% - 12px) 0,
    100% 12px,
    100% 100%,
    12px 100%,
    0 calc(100% - 12px)
  );
}
.aw-verdict-kicker {
  margin: 0 0 4px;
  font-size: 12px;
  font-weight: 600;
  color: var(--ui-text-muted);
}
.aw-verdict-title {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
  line-height: 1.4;
  overflow-wrap: anywhere;
}
.aw-verdict-direction {
  margin: 8px 0 0;
  font-size: 15px;
  font-weight: 600;
}
.aw-verdict-details {
  margin: 8px 0 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.aw-answers {
  display: grid;
  gap: 14px;
  margin-top: 16px;
}
.aw-answer {
  display: grid;
  gap: 4px;
}
.aw-answer h2 {
  margin: 0;
  color: var(--ui-text-muted);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.aw-answer p {
  margin: 0;
  font-size: 14px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.aw-comparison {
  display: grid;
  gap: 10px;
  margin-top: 6px;
}
.aw-compare-sides {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}
.aw-compare-side {
  display: grid;
  gap: 5px;
  padding: 9px;
  border: 1px solid var(--ui-border);
  border-radius: 7px;
  color: inherit;
  background: var(--ui-bg-muted);
  text-align: left;
  cursor: pointer;
}
.aw-compare-side:hover,
.aw-compare-side.is-selected {
  border-color: var(--ui-primary);
  box-shadow: 0 0 0 1px var(--ui-primary);
}
.aw-compare-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 6px;
}
.aw-compare-head code {
  color: var(--ui-text-muted);
  font-size: 11px;
}
.aw-compare-head strong {
  font-variant-numeric: tabular-nums;
}
.aw-compare-formula code {
  overflow-wrap: anywhere;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
}
.aw-compare-source {
  color: var(--ui-text-muted);
  font-size: 11px;
}
.aw-compare-diff {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--ui-warning);
  border-radius: 6px;
  background: color-mix(in srgb, var(--ui-warning) 12%, var(--ui-bg-elevated));
  font-size: 13px;
}
.aw-compare-diff span {
  color: var(--ui-text-muted);
}
.aw-compare-diff strong {
  color: var(--ui-warning);
  font-variant-numeric: tabular-nums;
}
.aw-compare-note {
  margin: 0;
  color: var(--ui-text-muted);
  font-size: 12px;
  line-height: 1.45;
}
.aw-sheet-grid td.is-selected-cell {
  outline: 2px solid var(--ui-primary);
  outline-offset: -2px;
}
.aw-evidence-cells {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 4px;
}
.aw-evidence-cell {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 7px;
  border: 1px solid var(--ui-border);
  border-radius: 5px;
  background: var(--ui-bg-muted);
  font-size: 12px;
}
.aw-evidence-cell code {
  color: var(--ui-text-muted);
}
.aw-evidence-cell.is-error {
  border-color: var(--ui-warning);
  background: color-mix(in srgb, var(--ui-warning) 12%, var(--ui-bg-muted));
}
.aw-vor-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.aw-vor-head .aw-section-title {
  margin: 0;
}
.aw-source-tabs {
  align-items: flex-start;
  padding: 8px 12px 0;
}
.aw-vor-source {
  padding: 16px;
  overflow: auto;
}
.aw-vor-name {
  margin: 0 0 12px;
  font-size: 15px;
  font-weight: 600;
  line-height: 1.4;
  overflow-wrap: anywhere;
}
.aw-vor-cells {
  display: grid;
  gap: 6px;
}
.aw-vor-cell {
  display: grid;
  grid-template-columns: 48px minmax(0, 1fr);
  gap: 8px;
  padding: 7px 8px;
  border: 1px solid var(--ui-border);
  border-radius: 6px;
  background: var(--ui-bg-elevated);
  font-size: 13px;
  overflow-wrap: anywhere;
}
.aw-vor-cell code {
  color: var(--ui-text-muted);
}
.aw-vor-cell.is-error {
  border-color: var(--ui-warning);
  background: color-mix(in srgb, var(--ui-warning) 12%, var(--ui-bg-elevated));
}
.aw-vor-formula {
  display: grid;
  gap: 4px;
  margin-top: 12px;
  padding: 10px;
  border: 1px solid var(--ui-border);
  border-radius: 6px;
  background: var(--ui-bg-elevated);
  font-size: 13px;
}
.aw-vor-formula span {
  color: var(--ui-text-muted);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.aw-vor-formula code {
  overflow-wrap: anywhere;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.aw-sheet-scroll {
  max-height: 52vh;
  overflow: auto;
  border: 1px solid var(--ui-border);
  border-radius: 8px;
  background: var(--ui-bg-elevated);
}
.aw-sheet-grid {
  border-collapse: separate;
  border-spacing: 0;
  min-width: 720px;
  width: max-content;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.aw-sheet-grid th,
.aw-sheet-grid td {
  border-right: 1px solid var(--ui-border);
  border-bottom: 1px solid var(--ui-border);
  padding: 6px 8px;
  text-align: left;
  vertical-align: top;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
.aw-sheet-grid thead th {
  position: sticky;
  top: 0;
  z-index: 2;
  min-width: 64px;
  color: var(--ui-text-muted);
  background: var(--ui-bg-muted);
  font-weight: 600;
  text-align: center;
}
.aw-sheet-grid tbody th {
  position: sticky;
  left: 0;
  z-index: 1;
  min-width: 38px;
  color: var(--ui-text-muted);
  background: var(--ui-bg-muted);
  text-align: center;
  font-weight: 600;
}
.aw-sheet-grid .aw-sheet-corner {
  left: 0;
  z-index: 3;
  min-width: 38px;
}
.aw-sheet-grid td {
  min-width: 76px;
  max-width: 260px;
  cursor: cell;
  background: var(--ui-bg-elevated);
}
.aw-sheet-grid td.is-source {
  background: color-mix(in srgb, var(--ui-primary) 5%, var(--ui-bg-elevated));
}
.aw-sheet-grid td.is-error {
  border-color: var(--ui-warning);
  background: color-mix(in srgb, var(--ui-warning) 14%, var(--ui-bg-elevated));
  box-shadow: inset 0 0 0 1px var(--ui-warning);
}
.aw-sheet-grid tr.is-selected-row th {
  color: var(--ui-text-highlighted);
  background: color-mix(in srgb, var(--ui-primary) 12%, var(--ui-bg-muted));
}
.aw-section-title {
  margin: 0 0 8px;
  font-size: 14px;
  font-weight: 700;
  color: var(--ui-text-highlighted);
}
.aw-table-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.aw-note-card p {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.6;
}
.sheet-scroll {
  width: 100%;
  min-width: 0;
  overflow-x: auto;
  border: 1px solid var(--ui-border);
  border-radius: 8px;
  overscroll-behavior: contain;
}
.sheet {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  background: var(--ui-bg-elevated);
}
.sheet th,
.sheet td {
  border: 1px solid var(--ui-border);
  padding: 8px;
  text-align: left;
  vertical-align: top;
  overflow-wrap: anywhere;
}
.sheet th {
  color: var(--ui-text-muted);
  font-weight: 600;
}
.sheet .numbers {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
@media (max-width: 720px) {
  .aw-topbar {
    flex-wrap: wrap;
  }
  .aw-heading {
    order: 3;
    flex-basis: 100%;
  }
  .aw-tabs {
    display: flex;
  }
  .aw-main[data-tab='vor'] .aw-splitter > :has(> .aw-pdf),
  .aw-main[data-tab='pdf'] .aw-splitter > :has(> .aw-dossier-pane),
  .aw-main [role='separator'] {
    display: none;
  }
  .aw-pdf,
  .aw-dossier-pane {
    padding: 0;
  }
}
</style>
