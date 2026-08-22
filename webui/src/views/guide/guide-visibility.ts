import { readStorageItem, writeStorageItem } from '@/utils/safe-storage'
import type { StorageLike } from '@/utils/safe-storage'

export const QUICK_START_GUIDE_HIDDEN_KEY = 'hideGuide'
export const QUICK_START_GUIDE_PROGRESS_KEY = 'completedSteps'

export const isQuickStartGuideVisible = (storage: StorageLike | null | undefined) =>
  readStorageItem(storage, QUICK_START_GUIDE_HIDDEN_KEY) !== 'true'

export const hideQuickStartGuide = (storage: StorageLike | null | undefined) => {
  writeStorageItem(storage, QUICK_START_GUIDE_HIDDEN_KEY, 'true')
}

export const showQuickStartGuide = (storage: StorageLike | null | undefined) => {
  writeStorageItem(storage, QUICK_START_GUIDE_HIDDEN_KEY, 'false')
}

export const shouldShowQuickStartRestore = (guideVisible: boolean) => !guideVisible

export const resetQuickStartGuideProgress = (storage: StorageLike | null | undefined) => {
  writeStorageItem(storage, QUICK_START_GUIDE_PROGRESS_KEY, '{}')
}
