import oneflow as flow

def test_default_placement_scope(test_case):
    func_config = flow.FunctionConfig()
    func_config.default_placement_scope(flow.fixed_placement("cpu", "0:0"))
    @flow.function(func_config)
    def Foo():
        test_case.assertEqual("cpu", flow.placement.current_scope().default_device_tag)
        return flow.get_variable("w", (10,), initializer=flow.constant_initializer(1))
    Foo().get()

def test_config_setter_getter(test_case):
    func_config = flow.FunctionConfig()
    func_config.enable_inplace()
    test_case.assertEqual(func_config.function_desc.enable_inplace, True)
    test_case.assertEqual(func_config.function_desc.enable_eager_execution, False)
    func_config.enable_eager_execution(True)
    test_case.assertEqual(func_config.function_desc.enable_eager_execution, True)
    func_config.enable_eager_execution(False)
    test_case.assertEqual(func_config.function_desc.enable_eager_execution, False)
    func_config.enable_eager_execution()
    test_case.assertEqual(func_config.function_desc.enable_eager_execution, True)
