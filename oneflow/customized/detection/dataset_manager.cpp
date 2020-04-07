#include "oneflow/customized/detection/dataset_manager.h"

namespace oneflow {

namespace detection {

std::shared_ptr<Dataset> DatasetManager::Get(const std::string& dataset_name) {
  std::unique_lock<std::mutex> lck_(mtx_);
  return dataset_map_.at(dataset_name);
}

std::shared_ptr<Dataset> DatasetManager::GetOrCreateDataset(const DetectionDatasetProto& proto) {
  std::unique_lock<std::mutex> lck_(mtx_);
  auto it = dataset_map_.find(proto.name());
  if (it == dataset_map_.end()) {
    Dataset* dataset = NewObj<Dataset>(proto.dataset_conf_case(), proto);
    std::shared_ptr<Dataset> dataset_ptr;
    dataset_ptr.reset(dataset);
    dataset_map_.emplace(proto.name(), dataset_ptr);
    return dataset_ptr;
  }
  return it->second;
}

}  // namespace detection

}  // namespace oneflow