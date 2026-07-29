import { useState, useCallback, useEffect, useRef } from 'react'
import { FileRejection } from 'react-dropzone'
import Button from '@/components/ui/Button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger
} from '@/components/ui/Dialog'
import FileUploader from '@/components/ui/FileUploader'
import { toast } from 'sonner'
import { errorMessage, cn } from '@/lib/utils'
import { uploadDocument, getProfiles } from '@/api/lightrag'
import type { TextChunkingStrategy } from '@/api/lightrag'

import { UploadIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

interface UploadDocumentsDialogProps {
  onDocumentsUploaded?: () => Promise<void>
  /**
   * Fired once per batch as soon as the first file is accepted by the server.
   * Lets the parent start its activity probe as early as possible (rather
   * than waiting for the whole sequential batch to finish).
   */
  onUploadBatchAccepted?: () => void
}

export default function UploadDocumentsDialog({
  onDocumentsUploaded,
  onUploadBatchAccepted
}: UploadDocumentsDialogProps) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
  const [progresses, setProgresses] = useState<Record<string, number>>({})
  const [fileErrors, setFileErrors] = useState<Record<string, string>>({})
  const [chunkingStrategy, setChunkingStrategy] = useState<TextChunkingStrategy>('fixed_token')
  const [profileName, setProfileName] = useState('')
  const [availableProfiles, setAvailableProfiles] = useState<string[]>([])
  const [profilesLoading, setProfilesLoading] = useState(false)
  const [chunkingParamsJson, setChunkingParamsJson] = useState('')
  const [skipGraph, setSkipGraph] = useState(false)

  const handleRejectedFiles = useCallback(
    (rejectedFiles: FileRejection[]) => {
      // Process rejected files and add them to fileErrors
      rejectedFiles.forEach(({ file, errors }) => {
        // Get the first error message
        let errorMsg = errors[0]?.message || t('documentPanel.uploadDocuments.fileUploader.fileRejected', { name: file.name })

        // Simplify error message for unsupported file types
        if (errorMsg.includes('file-invalid-type')) {
          errorMsg = t('documentPanel.uploadDocuments.fileUploader.unsupportedType')
        }

        // Set progress to 100% to display error message
        setProgresses((pre) => ({
          ...pre,
          [file.name]: 100
        }))

        // Add error message to fileErrors
        setFileErrors(prev => ({
          ...prev,
          [file.name]: errorMsg
        }))
      })
    },
    [setProgresses, setFileErrors, t]
  )

  // Fetch profiles eagerly when dialog opens, or lazily when structure_aware is selected.
  // Uses a ref gate to prevent infinite re-fetch loops when the API call fails
  // (e.g. /documents/profiles endpoint unavailable in older backends).
  const profilesFetchingRef = useRef(false)

  useEffect(() => {
    const shouldFetch = (open || chunkingStrategy === 'structure_aware') &&
      availableProfiles.length === 0 &&
      !profilesFetchingRef.current
    if (shouldFetch) {
      profilesFetchingRef.current = true
      setProfilesLoading(true)
      getProfiles()
        .then(setAvailableProfiles)
        .catch((err) => console.error('Failed to fetch profiles:', err))
        .finally(() => {
          setProfilesLoading(false)
          profilesFetchingRef.current = false
        })
    }
  }, [open, chunkingStrategy, availableProfiles.length])

  const handleDocumentsUpload = useCallback(
    async (filesToUpload: File[]) => {
      setIsUploading(true)
      let hasSuccessfulUpload = false

      // Only clear errors for files that are being uploaded, keep errors for rejected files
      setFileErrors(prev => {
        const newErrors = { ...prev };
        filesToUpload.forEach(file => {
          delete newErrors[file.name];
        });
        return newErrors;
      });

      // Show uploading toast
      const toastId = toast.loading(t('documentPanel.uploadDocuments.batch.uploading'))

      try {
        // Track errors locally to ensure we have the final state
        const uploadErrors: Record<string, string> = {}
        let batchProbeTriggered = false

        // Create a collator that supports Chinese sorting
        const collator = new Intl.Collator(['zh-CN', 'en'], {
          sensitivity: 'accent',  // consider basic characters, accents, and case
          numeric: true           // enable numeric sorting, e.g., "File 10" will be after "File 2"
        });
        const sortedFiles = [...filesToUpload].sort((a, b) =>
          collator.compare(a.name, b.name)
        );

        // Build chunking config JSON — parse user-supplied params
        let chunkingParams: Record<string, any> = {}
        if (chunkingParamsJson.trim()) {
          try {
            chunkingParams = JSON.parse(chunkingParamsJson)
          } catch {
            toast.error(t('documentPanel.uploadDocuments.invalidParamsJson'))
            setIsUploading(false)
            return
          }
        }
        const chunkingConfig = { strategy: chunkingStrategy, params: chunkingParams }
        const chunkingJson = JSON.stringify(chunkingConfig)
        const effectiveProfile = chunkingStrategy === 'structure_aware' ? profileName : undefined

        // Upload files in sequence, not parallel
        for (const file of sortedFiles) {
          try {
            // Initialize upload progress
            setProgresses((pre) => ({
              ...pre,
              [file.name]: 0
            }))

            const result = await uploadDocument(
              file,
              (percentCompleted: number) => {
              console.debug(t('documentPanel.uploadDocuments.single.uploading', { name: file.name, percent: percentCompleted }))
              setProgresses((pre) => ({
                ...pre,
                [file.name]: percentCompleted
              }))
            },
              chunkingJson,
              effectiveProfile,
              skipGraph
            )

            if (result.status !== 'success') {
              uploadErrors[file.name] = result.message
              setFileErrors(prev => ({
                ...prev,
                [file.name]: result.message
              }))
            } else {
              // Mark that we had at least one successful upload
              hasSuccessfulUpload = true
              if (!batchProbeTriggered) {
                batchProbeTriggered = true
                onUploadBatchAccepted?.()
              }
            }
          } catch (err) {
            console.error(`Upload failed for ${file.name}:`, err)

            // Handle HTTP errors, including 400 errors
            let errorMsg = errorMessage(err)
            const duplicateFileMsg = t('documentPanel.uploadDocuments.fileUploader.duplicateFile')

            // If it's an axios error with response data, try to extract more detailed error info
            if (err && typeof err === 'object' && 'response' in err) {
              const axiosError = err as { response?: { status: number, data?: { detail?: string } } }
              const status = axiosError.response?.status
              const detail = axiosError.response?.data?.detail
              if (status === 409) {
                // Server now rejects same-name uploads with HTTP 409 instead of
                // returning a 200 ``status="duplicated"`` payload.  Map the most
                // common cases (existing record / file in INPUT dir) back to the
                // dedicated "duplicate file" UI affordance, and surface other
                // 409 reasons (pipeline busy / scanning) verbatim from the
                // server detail so users can tell why they were rejected.
                if (
                  typeof detail === 'string' &&
                  (/already contains/i.test(detail) || /Status:/i.test(detail))
                ) {
                  errorMsg = duplicateFileMsg
                } else {
                  errorMsg = detail || errorMsg
                }
              } else if (status === 400) {
                errorMsg = detail || errorMsg
              }

              // Set progress to 100% to display error message
              setProgresses((pre) => ({
                ...pre,
                [file.name]: 100
              }))
            }

            // Record error message in both local tracking and state
            uploadErrors[file.name] = errorMsg
            setFileErrors(prev => ({
              ...prev,
              [file.name]: errorMsg
            }))
          }
        }

        // Check if any files failed to upload using our local tracking
        const hasErrors = Object.keys(uploadErrors).length > 0

        // Update toast status
        if (hasErrors) {
          toast.error(t('documentPanel.uploadDocuments.batch.error'), { id: toastId })
        } else {
          toast.success(t('documentPanel.uploadDocuments.batch.success'), { id: toastId })
        }

        // Only update if at least one file was uploaded successfully
        if (hasSuccessfulUpload) {
          // Refresh document list
          if (onDocumentsUploaded) {
            onDocumentsUploaded().catch(err => {
              console.error('Error refreshing documents:', err)
            })
          }
        }
      } catch (err) {
        console.error('Unexpected error during upload:', err)
        toast.error(t('documentPanel.uploadDocuments.generalError', { error: errorMessage(err) }), { id: toastId })
      } finally {
        setIsUploading(false)
      }
    },
    [setIsUploading, setProgresses, setFileErrors, t, onDocumentsUploaded, onUploadBatchAccepted, chunkingStrategy, profileName, chunkingParamsJson, skipGraph]
  )

  return (
    <Dialog
      open={open}
      onOpenChange={(open) => {
        if (isUploading) {
          return
        }
        if (!open) {
          setProgresses({})
          setFileErrors({})
        }
        setOpen(open)
      }}
    >
      <DialogTrigger asChild>
        <Button variant="default" side="bottom" tooltip={t('documentPanel.uploadDocuments.tooltip')} size="sm">
          <UploadIcon /> {t('documentPanel.uploadDocuments.button')}
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-xl" onCloseAutoFocus={(e) => e.preventDefault()}>
        <DialogHeader>
          <DialogTitle>{t('documentPanel.uploadDocuments.title')}</DialogTitle>
          <DialogDescription>
            {t('documentPanel.uploadDocuments.description')}
          </DialogDescription>
        </DialogHeader>

        {/* Chunking strategy & profile selectors */}
        <div className="flex flex-col gap-3 px-1 pb-1">
          <div className="flex items-center gap-2">
            <label className="text-sm font-medium whitespace-nowrap">
              {t('documentPanel.uploadDocuments.chunkingStrategy')}
            </label>
            <select
              value={chunkingStrategy}
              onChange={(e) => {
                setChunkingStrategy(e.target.value as TextChunkingStrategy)
                setProfileName('')
              }}
              disabled={isUploading}
              className="border-input bg-background ring-offset-background focus:ring-ring flex h-9 w-full rounded-md border px-3 py-1 text-sm focus:ring-2 focus:ring-offset-2 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
            >
              <option value="fixed_token">{t('documentPanel.uploadDocuments.strategy.fixed_token')}</option>
              <option value="recursive_character">{t('documentPanel.uploadDocuments.strategy.recursive_character')}</option>
              <option value="semantic_vector">{t('documentPanel.uploadDocuments.strategy.semantic_vector')}</option>
              <option value="paragraph_semantic">{t('documentPanel.uploadDocuments.strategy.paragraph_semantic')}</option>
              <option value="structure_aware">{t('documentPanel.uploadDocuments.strategy.structure_aware')}</option>
            </select>
          </div>

          <div className={cn(
            'flex items-center gap-2',
            (chunkingStrategy === 'recursive_character' || chunkingStrategy === 'semantic_vector') ? '' : 'hidden'
          )}>
            <label className="text-sm font-medium whitespace-nowrap">
              {t('documentPanel.uploadDocuments.chunkingParams')}
            </label>
            <input
              type="text"
              value={chunkingParamsJson}
              onChange={(e) => setChunkingParamsJson(e.target.value)}
              placeholder={t('documentPanel.uploadDocuments.chunkingParamsHint')}
              disabled={isUploading}
              className="border-input bg-background ring-offset-background focus:ring-ring flex h-9 w-full rounded-md border px-3 py-1 text-sm focus:ring-2 focus:ring-offset-2 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
            />
          </div>

          <div className={cn(
            'flex items-center gap-2',
            chunkingStrategy === 'structure_aware' ? '' : 'hidden'
          )}>
            <label className="text-sm font-medium whitespace-nowrap">
              {t('documentPanel.uploadDocuments.parserProfile')}
            </label>
            <select
              value={profileName}
              onChange={(e) => setProfileName(e.target.value)}
              disabled={isUploading || profilesLoading}
              className="border-input bg-background ring-offset-background focus:ring-ring flex h-9 w-full rounded-md border px-3 py-1 text-sm focus:ring-2 focus:ring-offset-2 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
            >
              {profilesLoading ? (
                <option value="">{t('documentPanel.uploadDocuments.profiles.loading')}</option>
              ) : availableProfiles.length === 0 ? (
                <option value="">{t('documentPanel.uploadDocuments.profiles.empty')}</option>
              ) : (
                <>
                  <option value="">{t('documentPanel.uploadDocuments.profiles.selectPlaceholder')}</option>
                  {availableProfiles.map((p) => (
                    <option key={p} value={p}>{p}</option>
                  ))}
                </>
              )}
            </select>
          </div>

          <div className="flex items-center gap-2 px-1">
            <input
              type="checkbox"
              id="skip-graph-upload"
              checked={skipGraph}
              onChange={(e) => setSkipGraph(e.target.checked)}
              disabled={isUploading}
              className="h-4 w-4"
            />
            <label htmlFor="skip-graph-upload" className="text-sm cursor-pointer select-none">
              {t('documentPanel.uploadDocuments.skipGraph')}
            </label>
          </div>
        </div>

        <FileUploader
          maxFileCount={Infinity}
          maxSize={200 * 1024 * 1024}
          description={t('documentPanel.uploadDocuments.fileTypes')}
          onUpload={handleDocumentsUpload}
          onReject={handleRejectedFiles}
          progresses={progresses}
          fileErrors={fileErrors}
          disabled={isUploading}
        />
      </DialogContent>
    </Dialog>
  )
}
