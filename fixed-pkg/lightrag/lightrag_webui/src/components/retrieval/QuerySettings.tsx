import { useCallback, useEffect, useMemo, useState } from 'react'
import { QueryMode, QueryRequest } from '@/api/lightrag'
// Removed unused import for Text component
import Checkbox from '@/components/ui/Checkbox'
import Input from '@/components/ui/Input'
import UserPromptInputWithHistory from '@/components/ui/UserPromptInputWithHistory'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue
} from '@/components/ui/Select'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip'
import { useSettingsStore } from '@/stores/settings'
import { useTranslation } from 'react-i18next'
import { RotateCcw, Plus, X } from 'lucide-react'
import { cn } from '@/lib/utils'

const ResetButton = ({ onClick, title }: { onClick: () => void; title: string }) => (
  <TooltipProvider>
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          onClick={onClick}
          className="mr-1 p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
          title={title}
        >
          <RotateCcw className="h-3 w-3 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200" />
        </button>
      </TooltipTrigger>
      <TooltipContent side="left">
        <p>{title}</p>
      </TooltipContent>
    </Tooltip>
  </TooltipProvider>
)

export default function QuerySettings() {
  const { t } = useTranslation()
  const querySettings = useSettingsStore((state) => state.querySettings)
  const userPromptHistory = useSettingsStore((state) => state.userPromptHistory)

  const handleChange = useCallback((key: keyof QueryRequest, value: any) => {
    useSettingsStore.getState().updateQuerySettings({ [key]: value })
  }, [])

  const handleSelectFromHistory = useCallback((prompt: string) => {
    handleChange('user_prompt', prompt)
  }, [handleChange])

  const handleDeleteFromHistory = useCallback((index: number) => {
    const newHistory = [...userPromptHistory]
    newHistory.splice(index, 1)
    useSettingsStore.getState().setUserPromptHistory(newHistory)
  }, [userPromptHistory])

  // Default values for reset functionality
  const defaultValues = useMemo(() => ({
    mode: 'mix' as QueryMode,
    top_k: 40,
    chunk_top_k: 20,
    max_entity_tokens: 6000,
    max_relation_tokens: 8000,
    max_total_tokens: 30000
  }), [])

  const handleReset = useCallback((key: keyof typeof defaultValues) => {
    handleChange(key, defaultValues[key])
  }, [handleChange, defaultValues])

  // --- Metadata filter rule list ---
  type FilterRule = {
    id: string
    key: string
    operator: string   // = != > >= < <= contains in
    value: string
    logic: 'AND' | 'OR' // logic connecting to NEXT rule
  }

  const [filterRules, setFilterRules] = useState<FilterRule[]>([])
  const [filterExpanded, setFilterExpanded] = useState(false)
  let _ruleIdCounter = 0
  const nextRuleId = () => `r${Date.now()}_${_ruleIdCounter++}`

  const addFilterRule = useCallback(() => {
    setFilterRules(prev => [...prev, {
      id: nextRuleId(),
      key: '',
      operator: '=',
      value: '',
      logic: 'AND'
    }])
  }, [])

  const removeFilterRule = useCallback((id: string) => {
    setFilterRules(prev => prev.filter(r => r.id !== id))
  }, [])

  const updateFilterRule = useCallback((id: string, field: keyof FilterRule, val: string) => {
    setFilterRules(prev => prev.map(r => r.id === id ? { ...r, [field]: val } : r))
  }, [])

  // Build metadata_filter JSON from rules and push to store
  const buildAndApplyFilter = useCallback((rules: FilterRule[]) => {
    if (rules.length === 0 || rules.every(r => !r.key.trim() || !r.value.trim())) {
      handleChange('metadata_filter', undefined)
      return
    }

    // Group consecutive rules sharing the same logic operator.
    // The logic on rule N connects it to rule N+1.
    const rebuilt: { logic: 'AND' | 'OR', rules: FilterRule[] }[] = []
    for (const r of rules) {
      if (!r.key.trim() || !r.value.trim()) continue
      const last = rebuilt[rebuilt.length - 1]
      if (last && last.logic === r.logic) {
        last.rules.push(r)
      } else {
        rebuilt.push({ logic: r.logic, rules: [r] })
      }
    }

    if (rebuilt.length === 0) {
      handleChange('metadata_filter', undefined)
      return
    }

    // Helper: build a field-value pair from a rule
    const buildFieldValue = (r: FilterRule): [string, any] => {
      const v = r.value.trim()
      switch (r.operator) {
        case '!=': return [r.key, { ne: v }]
        case '>': return [r.key, { gt: parseFloat(v) || v }]
        case '>=': return [r.key, { gte: parseFloat(v) || v }]
        case '<': return [r.key, { lt: parseFloat(v) || v }]
        case '<=': return [r.key, { lte: parseFloat(v) || v }]
        case 'contains': return [r.key, { contains: v }]
        case 'in': {
          const vals = v.split(',').map(s => s.trim()).filter(Boolean)
          // Try parse numbers
          const parsed = vals.map(x => { const n = parseFloat(x); return isNaN(n) ? x : n })
          return [r.key, parsed.length === 1 ? parsed[0] : parsed]
        }
        default: { // '='
          if (v === 'true') return [r.key, true]
          if (v === 'false') return [r.key, false]
          const num = parseFloat(v)
          return [r.key, isNaN(num) ? v : num]
        }
      }
    }

    if (rebuilt.length === 1) {
      // Single logic group
      const group = rebuilt[0]
      if (group.logic === 'AND') {
        const flat: Record<string, any> = {}
        for (const r of group.rules) {
          const [k, v] = buildFieldValue(r)
          flat[k] = v
        }
        handleChange('metadata_filter', flat)
      } else {
        // All OR
        handleChange('metadata_filter', {
          $or: group.rules.map(r => {
            const [k, v] = buildFieldValue(r)
            return { [k]: v }
          })
        })
      }
    } else {
      // Mixed: multiple groups → wrap in $or
      const orParts = rebuilt.map(group => {
        const flat: Record<string, any> = {}
        for (const r of group.rules) {
          const [k, v] = buildFieldValue(r)
          flat[k] = v
        }
        return flat
      })
      handleChange('metadata_filter', { $or: orParts })
    }
  }, [handleChange])

  // Apply filter whenever rules change
  useEffect(() => {
    buildAndApplyFilter(filterRules)
  }, [filterRules, buildAndApplyFilter])

  // Mix offers the best retrieval coverage; Bypass intentionally skips retrieval.
  // Warn only for the narrower-coverage modes (hybrid/naive/local/global).
  const showQualityWarning =
    querySettings.mode !== 'mix' && querySettings.mode !== 'bypass'

  return (
    <Card className="flex shrink-0 flex-col w-[280px]">
      <CardHeader className="px-4 pt-4 pb-2">
        <CardTitle>{t('retrievePanel.querySettings.parametersTitle')}</CardTitle>
        <CardDescription className="sr-only">{t('retrievePanel.querySettings.parametersDescription')}</CardDescription>
      </CardHeader>
      <CardContent className="m-0 flex grow flex-col p-0 text-xs">
        <div className="relative size-full">
          <div className="absolute inset-0 flex flex-col gap-2 overflow-auto px-2 pr-2">
            {/* User Prompt - Moved to top for better dropdown space */}
            <>
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <label htmlFor="user_prompt" className="ml-1 cursor-help">
                      {t('retrievePanel.querySettings.userPrompt')}
                    </label>
                  </TooltipTrigger>
                  <TooltipContent side="left">
                    <p>{t('retrievePanel.querySettings.userPromptTooltip')}</p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
              <div>
                <UserPromptInputWithHistory
                  id="user_prompt"
                  value={querySettings.user_prompt || ''}
                  onChange={(value) => handleChange('user_prompt', value)}
                  onSelectFromHistory={handleSelectFromHistory}
                  onDeleteFromHistory={handleDeleteFromHistory}
                  history={userPromptHistory}
                  placeholder={t('retrievePanel.querySettings.userPromptPlaceholder')}
                  className="h-9"
                />
              </div>
            </>

            {/* Query Mode */}
            <>
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <label htmlFor="query_mode_select" className="ml-1 cursor-help">
                      {t('retrievePanel.querySettings.queryMode')}
                    </label>
                  </TooltipTrigger>
                  <TooltipContent side="left">
                    <p>{t('retrievePanel.querySettings.queryModeTooltip')}</p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
              <div className="flex items-center gap-1">
                <Select
                  value={querySettings.mode}
                  onValueChange={(v) => handleChange('mode', v as QueryMode)}
                >
                  <SelectTrigger
                    id="query_mode_select"
                    className={cn(
                      'hover:bg-primary/5 h-9 cursor-pointer focus:ring-0 focus:ring-offset-0 focus:outline-0 active:right-0 flex-1 text-left [&>span]:break-all [&>span]:line-clamp-1',
                      showQualityWarning &&
                        'border-red-400 bg-red-100 text-red-700 hover:bg-red-100 dark:border-red-600 dark:bg-red-900/30 dark:text-red-300 dark:hover:bg-red-900/30'
                    )}
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      <SelectItem value="mix">{t('retrievePanel.querySettings.queryModeOptions.mix')}</SelectItem>
                      <SelectItem value="hybrid">{t('retrievePanel.querySettings.queryModeOptions.hybrid')}</SelectItem>
                      <SelectItem value="naive">{t('retrievePanel.querySettings.queryModeOptions.naive')}</SelectItem>
                      <SelectItem value="local">{t('retrievePanel.querySettings.queryModeOptions.local')}</SelectItem>
                      <SelectItem value="global">{t('retrievePanel.querySettings.queryModeOptions.global')}</SelectItem>
                      <SelectItem value="bypass">{t('retrievePanel.querySettings.queryModeOptions.bypass')}</SelectItem>
                    </SelectGroup>
                  </SelectContent>
                </Select>
                <ResetButton
                  onClick={() => handleReset('mode')}
                  title="Reset to default (Mix)"
                />
              </div>
              {showQualityWarning && (
                <p className="ml-1 text-red-600 dark:text-red-400">
                  {t('retrievePanel.querySettings.queryModeWarning')}
                </p>
              )}
            </>

            {/* Top K */}
            <>
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <label htmlFor="top_k" className="ml-1 cursor-help">
                      {t('retrievePanel.querySettings.topK')}
                    </label>
                  </TooltipTrigger>
                  <TooltipContent side="left">
                    <p>{t('retrievePanel.querySettings.topKTooltip')}</p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
              <div className="flex items-center gap-1">
                <Input
                  id="top_k"
                  type="number"
                  value={querySettings.top_k ?? ''}
                  onChange={(e) => {
                    const value = e.target.value
                    handleChange('top_k', value === '' ? '' : parseInt(value) || 0)
                  }}
                  onBlur={(e) => {
                    const value = e.target.value
                    if (value === '' || isNaN(parseInt(value))) {
                      handleChange('top_k', 40)
                    }
                  }}
                  min={1}
                  placeholder={t('retrievePanel.querySettings.topKPlaceholder')}
                  className="h-9 flex-1 pr-2 [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none [-moz-appearance:textfield]"
                />
                <ResetButton
                  onClick={() => handleReset('top_k')}
                  title="Reset to default"
                />
              </div>
            </>

            {/* Chunk Top K */}
            <>
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <label htmlFor="chunk_top_k" className="ml-1 cursor-help">
                      {t('retrievePanel.querySettings.chunkTopK')}
                    </label>
                  </TooltipTrigger>
                  <TooltipContent side="left">
                    <p>{t('retrievePanel.querySettings.chunkTopKTooltip')}</p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
              <div className="flex items-center gap-1">
                <Input
                  id="chunk_top_k"
                  type="number"
                  value={querySettings.chunk_top_k ?? ''}
                  onChange={(e) => {
                    const value = e.target.value
                    handleChange('chunk_top_k', value === '' ? '' : parseInt(value) || 0)
                  }}
                  onBlur={(e) => {
                    const value = e.target.value
                    if (value === '' || isNaN(parseInt(value))) {
                      handleChange('chunk_top_k', 20)
                    }
                  }}
                  min={1}
                  placeholder={t('retrievePanel.querySettings.chunkTopKPlaceholder')}
                  className="h-9 flex-1 pr-2 [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none [-moz-appearance:textfield]"
                />
                <ResetButton
                  onClick={() => handleReset('chunk_top_k')}
                  title="Reset to default"
                />
              </div>
            </>

            {/* Max Entity Tokens */}
            <>
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <label htmlFor="max_entity_tokens" className="ml-1 cursor-help">
                      {t('retrievePanel.querySettings.maxEntityTokens')}
                    </label>
                  </TooltipTrigger>
                  <TooltipContent side="left">
                    <p>{t('retrievePanel.querySettings.maxEntityTokensTooltip')}</p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
              <div className="flex items-center gap-1">
                <Input
                  id="max_entity_tokens"
                  type="number"
                  value={querySettings.max_entity_tokens ?? ''}
                  onChange={(e) => {
                    const value = e.target.value
                    handleChange('max_entity_tokens', value === '' ? '' : parseInt(value) || 0)
                  }}
                  onBlur={(e) => {
                    const value = e.target.value
                    if (value === '' || isNaN(parseInt(value))) {
                      handleChange('max_entity_tokens', 6000)
                    }
                  }}
                  min={1}
                  placeholder={t('retrievePanel.querySettings.maxEntityTokensPlaceholder')}
                  className="h-9 flex-1 pr-2 [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none [-moz-appearance:textfield]"
                />
                <ResetButton
                  onClick={() => handleReset('max_entity_tokens')}
                  title="Reset to default"
                />
              </div>
            </>

            {/* Max Relation Tokens */}
            <>
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <label htmlFor="max_relation_tokens" className="ml-1 cursor-help">
                      {t('retrievePanel.querySettings.maxRelationTokens')}
                    </label>
                  </TooltipTrigger>
                  <TooltipContent side="left">
                    <p>{t('retrievePanel.querySettings.maxRelationTokensTooltip')}</p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
              <div className="flex items-center gap-1">
                <Input
                  id="max_relation_tokens"
                  type="number"
                  value={querySettings.max_relation_tokens ?? ''}
                  onChange={(e) => {
                    const value = e.target.value
                    handleChange('max_relation_tokens', value === '' ? '' : parseInt(value) || 0)
                  }}
                  onBlur={(e) => {
                    const value = e.target.value
                    if (value === '' || isNaN(parseInt(value))) {
                      handleChange('max_relation_tokens', 8000)
                    }
                  }}
                  min={1}
                  placeholder={t('retrievePanel.querySettings.maxRelationTokensPlaceholder')}
                  className="h-9 flex-1 pr-2 [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none [-moz-appearance:textfield]"
                />
                <ResetButton
                  onClick={() => handleReset('max_relation_tokens')}
                  title="Reset to default"
                />
              </div>
            </>

            {/* Max Total Tokens */}
            <>
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <label htmlFor="max_total_tokens" className="ml-1 cursor-help">
                      {t('retrievePanel.querySettings.maxTotalTokens')}
                    </label>
                  </TooltipTrigger>
                  <TooltipContent side="left">
                    <p>{t('retrievePanel.querySettings.maxTotalTokensTooltip')}</p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
              <div className="flex items-center gap-1">
                <Input
                  id="max_total_tokens"
                  type="number"
                  value={querySettings.max_total_tokens ?? ''}
                  onChange={(e) => {
                    const value = e.target.value
                    handleChange('max_total_tokens', value === '' ? '' : parseInt(value) || 0)
                  }}
                  onBlur={(e) => {
                    const value = e.target.value
                    if (value === '' || isNaN(parseInt(value))) {
                      handleChange('max_total_tokens', 30000)
                    }
                  }}
                  min={1}
                  placeholder={t('retrievePanel.querySettings.maxTotalTokensPlaceholder')}
                  className="h-9 flex-1 pr-2 [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none [-moz-appearance:textfield]"
                />
                <ResetButton
                  onClick={() => handleReset('max_total_tokens')}
                  title="Reset to default"
                />
              </div>
            </>

            {/* Metadata Filter — collapsible rule list */}
            <>
              <button
                type="button"
                onClick={() => setFilterExpanded(!filterExpanded)}
                className="flex items-center gap-1 text-xs font-medium hover:underline"
              >
                <span>{filterExpanded ? '▾' : '▸'}</span>
                <span>{t('retrievePanel.querySettings.metadataFilter')}</span>
                {filterRules.length > 0 && (
                  <span className="rounded-full bg-blue-100 px-1.5 text-[10px] dark:bg-blue-900/30">
                    {filterRules.length}
                  </span>
                )}
              </button>

              {filterExpanded && (
                <div className="flex flex-col gap-1.5">
                  {/* Filter mode */}
                  <div className="flex items-center gap-1">
                    <span className="text-[10px] text-gray-400 w-8 shrink-0">模式</span>
                    <Select
                      value={querySettings.filter_mode || 'soft'}
                      onValueChange={(v) => handleChange('filter_mode', v as 'strict' | 'soft')}
                    >
                      <SelectTrigger className="h-7 flex-1 text-xs cursor-pointer focus:ring-0">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectGroup>
                          <SelectItem value="soft">{t('retrievePanel.querySettings.filterModeSoft')}</SelectItem>
                          <SelectItem value="strict">{t('retrievePanel.querySettings.filterModeStrict')}</SelectItem>
                        </SelectGroup>
                      </SelectContent>
                    </Select>
                  </div>

                  {/* Rule list */}
                  {filterRules.map((rule, idx) => (
                    <div key={rule.id} className="flex flex-col gap-1 rounded border border-gray-200 dark:border-gray-700 p-1.5">
                      {/* AND/OR toggle (not for first rule) */}
                      {idx > 0 && (
                        <div className="flex items-center gap-1">
                          <span className="text-[10px] text-gray-400 w-8 shrink-0">逻辑</span>
                          <button
                            type="button"
                            onClick={() => updateFilterRule(rule.id, 'logic', rule.logic === 'AND' ? 'OR' : 'AND')}
                            className={`rounded px-2 py-0.5 text-[10px] font-medium ${
                              rule.logic === 'AND'
                                ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300'
                                : 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-300'
                            }`}
                          >
                            {rule.logic}
                          </button>
                        </div>
                      )}

                      <div className="flex items-center gap-1">
                        <input
                          type="text"
                          value={rule.key}
                          onChange={(e) => updateFilterRule(rule.id, 'key', e.target.value)}
                          placeholder="字段"
                          className="h-7 w-[60px] shrink-0 rounded border border-input bg-background px-1.5 text-[11px]"
                        />
                        <select
                          value={rule.operator}
                          onChange={(e) => updateFilterRule(rule.id, 'operator', e.target.value)}
                          className="h-7 w-[60px] shrink-0 rounded border border-input bg-background px-0.5 text-[11px]"
                        >
                          <option value="=">=</option>
                          <option value="!=">≠</option>
                          <option value=">">&gt;</option>
                          <option value=">=">≥</option>
                          <option value="<">&lt;</option>
                          <option value="<=">≤</option>
                          <option value="contains">≈</option>
                          <option value="in">∈</option>
                        </select>
                        <input
                          type="text"
                          value={rule.value}
                          onChange={(e) => updateFilterRule(rule.id, 'value', e.target.value)}
                          placeholder="值"
                          className="h-7 flex-1 rounded border border-input bg-background px-1.5 text-[11px]"
                        />
                        <button
                          type="button"
                          onClick={() => removeFilterRule(rule.id)}
                          className="shrink-0 text-gray-400 hover:text-red-500"
                          title="删除"
                        >
                          <X className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </div>
                  ))}

                  {/* Add rule button */}
                  <button
                    type="button"
                    onClick={addFilterRule}
                    className="flex items-center gap-1 text-[11px] text-blue-600 hover:text-blue-800 dark:text-blue-400"
                  >
                    <Plus className="h-3 w-3" /> {t('retrievePanel.querySettings.metadataFilterAddRule')}
                  </button>
                </div>
              )}
            </>

            {/* Toggle Options */}
            <>
              <div className="flex items-center gap-2">
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <label htmlFor="enable_rerank" className="flex-1 ml-1 cursor-help">
                        {t('retrievePanel.querySettings.enableRerank')}
                      </label>
                    </TooltipTrigger>
                    <TooltipContent side="left">
                      <p>{t('retrievePanel.querySettings.enableRerankTooltip')}</p>
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
                <Checkbox
                  className="mr-10 cursor-pointer"
                  id="enable_rerank"
                  checked={querySettings.enable_rerank}
                  onCheckedChange={(checked) => handleChange('enable_rerank', checked)}
                />
              </div>

              <div className="flex items-center gap-2">
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <label htmlFor="only_need_context" className="flex-1 ml-1 cursor-help">
                        {t('retrievePanel.querySettings.onlyNeedContext')}
                      </label>
                    </TooltipTrigger>
                    <TooltipContent side="left">
                      <p>{t('retrievePanel.querySettings.onlyNeedContextTooltip')}</p>
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
                <Checkbox
                  className="mr-10 cursor-pointer"
                  id="only_need_context"
                  checked={querySettings.only_need_context}
                  onCheckedChange={(checked) => {
                    handleChange('only_need_context', checked)
                    if (checked) {
                      handleChange('only_need_prompt', false)
                    }
                  }}
                />
              </div>

              <div className="flex items-center gap-2">
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <label htmlFor="only_need_prompt" className="flex-1 ml-1 cursor-help">
                        {t('retrievePanel.querySettings.onlyNeedPrompt')}
                      </label>
                    </TooltipTrigger>
                    <TooltipContent side="left">
                      <p>{t('retrievePanel.querySettings.onlyNeedPromptTooltip')}</p>
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
                <Checkbox
                  className="mr-10 cursor-pointer"
                  id="only_need_prompt"
                  checked={querySettings.only_need_prompt}
                  onCheckedChange={(checked) => {
                    handleChange('only_need_prompt', checked)
                    if (checked) {
                      handleChange('only_need_context', false)
                    }
                  }}
                />
              </div>

              <div className="flex items-center gap-2">
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <label htmlFor="stream" className="flex-1 ml-1 cursor-help">
                        {t('retrievePanel.querySettings.streamResponse')}
                      </label>
                    </TooltipTrigger>
                    <TooltipContent side="left">
                      <p>{t('retrievePanel.querySettings.streamResponseTooltip')}</p>
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
                <Checkbox
                  className="mr-10 cursor-pointer"
                  id="stream"
                  checked={querySettings.stream}
                  onCheckedChange={(checked) => handleChange('stream', checked)}
                />
              </div>
            </>

          </div>
        </div>
      </CardContent>
    </Card>
  )
}
